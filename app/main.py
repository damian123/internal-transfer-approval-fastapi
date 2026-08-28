from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.db import get_session
from app.schemas import ApprovalCreate, ErrorRead, TransferCreate, TransferRead
from app.service import (
    DomainError,
    create_transfer,
    execute_transfer,
    load_transfer,
    record_approval,
)

SessionDep = Annotated[Session, Depends(get_session)]
IdempotencyKey = Annotated[str, Header(min_length=8, max_length=120)]


app = FastAPI(
    title="Internal Transfer Approval Service",
    version="0.1.0",
    description="Synthetic portfolio demonstration; not a production payment system.",
)


@app.exception_handler(DomainError)
async def handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorRead(code=exc.code, message=exc.message).model_dump(),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/transfers", response_model=TransferRead, status_code=201)
def submit_transfer(
    command: TransferCreate,
    response: Response,
    session: SessionDep,
) -> TransferRead:
    transfer, created = create_transfer(session, command)
    if not created:
        response.status_code = 200
    return TransferRead.model_validate(transfer)


@app.get("/transfers/{transfer_id}", response_model=TransferRead)
def get_transfer(transfer_id: UUID, session: SessionDep) -> TransferRead:
    return TransferRead.model_validate(load_transfer(session, transfer_id))


@app.post("/transfers/{transfer_id}/decisions", response_model=TransferRead)
def decide_transfer(
    transfer_id: UUID,
    command: ApprovalCreate,
    session: SessionDep,
) -> TransferRead:
    return TransferRead.model_validate(record_approval(session, transfer_id, command))


@app.post("/transfers/{transfer_id}/execute", response_model=TransferRead)
def execute_approved_transfer(
    transfer_id: UUID,
    response: Response,
    idempotency_key: IdempotencyKey,
    session: SessionDep,
) -> TransferRead:
    transfer, created = execute_transfer(session, transfer_id, idempotency_key)
    if not created:
        response.headers["Idempotent-Replay"] = "true"
    return TransferRead.model_validate(transfer)
