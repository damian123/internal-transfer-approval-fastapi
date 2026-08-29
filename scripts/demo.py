"""Execute the synthetic happy path and a duplicate-safe retry."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app


@contextmanager
def demo_client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)


def emit(step: str, response: Response) -> None:
    body: dict[str, Any] = response.json()
    print(
        json.dumps(
            {
                "step": step,
                "http_status": response.status_code,
                "transfer_status": body.get("status"),
                "approvals": len(body.get("approvals", [])),
                "executions": len(body.get("executions", [])),
                "idempotent_replay": response.headers.get("Idempotent-Replay") == "true",
            },
            sort_keys=True,
        )
    )


def main() -> None:
    transfer = {
        "client_request_id": "demo-transfer-001",
        "requester": "treasury.operator",
        "source_account": "operating-jpy",
        "destination_account": "custody-usd",
        "currency": "USD",
        "amount": "250000.00",
        "purpose": "Synthetic portfolio walkthrough",
    }

    with demo_client() as client:
        created = client.post("/transfers", json=transfer)
        emit("request_submitted", created)
        transfer_id = created.json()["id"]

        for approver in ("risk.approver", "finance.approver"):
            decision = client.post(
                f"/transfers/{transfer_id}/decisions",
                json={
                    "approver": approver,
                    "decision": "approve",
                    "reason": "Synthetic policy checks passed",
                },
            )
            emit(f"approved_by_{approver}", decision)

        execution_headers = {"Idempotency-Key": "demo-execution-001"}
        executed = client.post(f"/transfers/{transfer_id}/execute", headers=execution_headers)
        emit("execution_completed", executed)

        replay = client.post(f"/transfers/{transfer_id}/execute", headers=execution_headers)
        emit("execution_replayed_safely", replay)


if __name__ == "__main__":
    main()
