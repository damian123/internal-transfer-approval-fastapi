import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event, get_ident
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models import ExecutionAttempt, TransferRequest, TransferStatus
from app.schemas import ApprovalCreate, TransferCreate
from app.service import (
    DomainError,
    create_transfer,
    execute_transfer,
    load_transfer,
    record_approval,
)

POSTGRES_TEST_DATABASE_URL = "POSTGRES_TEST_DATABASE_URL"


def transfer_command(request_id: str, *, amount: str = "250000.00") -> TransferCreate:
    return TransferCreate(
        client_request_id=request_id,
        requester="treasury.operator",
        source_account="operating-jpy",
        destination_account="custody-usd",
        currency="USD",
        amount=Decimal(amount),
        purpose="PostgreSQL concurrency verification",
    )


def approval(approver: str, decision: str = "approve") -> ApprovalCreate:
    return ApprovalCreate(
        approver=approver,
        decision=decision,
        reason="Deterministic PostgreSQL race test",
    )


@pytest.fixture(scope="session")
def postgres_engine() -> Engine:
    database_url = os.getenv(POSTGRES_TEST_DATABASE_URL)
    if database_url is None:
        pytest.skip(f"set {POSTGRES_TEST_DATABASE_URL} to run PostgreSQL concurrency tests")

    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() != "postgresql":
        pytest.fail(f"{POSTGRES_TEST_DATABASE_URL} must use PostgreSQL")
    if parsed_url.database is None or not parsed_url.database.endswith("_test"):
        pytest.fail(f"{POSTGRES_TEST_DATABASE_URL} must identify a dedicated *_test database")

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")

    engine = create_engine(database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def postgres_sessions(postgres_engine: Engine) -> sessionmaker[Session]:
    with postgres_engine.begin() as connection:
        connection.execute(
            text("TRUNCATE execution_attempts, approval_decisions, transfer_requests CASCADE")
        )
    return sessionmaker(bind=postgres_engine, autoflush=False, expire_on_commit=False)


def capture_domain_error(operation: Callable[[], object]) -> DomainError:
    try:
        operation()
    except DomainError as exc:
        return exc
    raise AssertionError("operation did not raise DomainError")


def lock_transfer(session: Session, transfer_id: UUID) -> None:
    locked = session.scalar(
        select(TransferRequest).where(TransferRequest.id == transfer_id).with_for_update()
    )
    assert locked is not None


def test_conflicting_create_race_compares_the_committed_payload(
    postgres_engine: Engine,
    postgres_sessions: sessionmaker[Session],
) -> None:
    winner = transfer_command("postgres-create-race", amount="250000.00")
    contender = transfer_command("postgres-create-race", amount="250001.00")
    select_seen = Event()
    release_contender = Event()
    contender_thread_id: int | None = None

    def pause_after_idempotency_lookup(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            get_ident() == contender_thread_id
            and statement.lstrip().upper().startswith("SELECT")
            and "transfer_requests.client_request_id" in statement
        ):
            select_seen.set()
            if not release_contender.wait(timeout=5):
                raise AssertionError("timed out coordinating the create race")

    def submit_contender() -> DomainError:
        nonlocal contender_thread_id
        contender_thread_id = get_ident()
        with postgres_sessions() as session:
            return capture_domain_error(lambda: create_transfer(session, contender))

    event.listen(postgres_engine, "after_cursor_execute", pause_after_idempotency_lookup)
    try:
        with postgres_sessions() as winner_session:
            winner_row = TransferRequest(**winner.model_dump())
            winner_session.add(winner_row)
            winner_session.flush()

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(submit_contender)
                assert select_seen.wait(timeout=5)
                winner_session.commit()
                release_contender.set()
                error = future.result(timeout=5)
    finally:
        release_contender.set()
        event.remove(postgres_engine, "after_cursor_execute", pause_after_idempotency_lookup)

    assert error.code == "idempotency_payload_mismatch"
    with postgres_sessions() as session:
        persisted = session.scalar(
            select(TransferRequest).where(
                TransferRequest.client_request_id == winner.client_request_id
            )
        )
        assert persisted is not None
        assert persisted.amount == winner.amount


def test_rejection_remains_final_while_an_approval_waits_on_the_row_lock(
    postgres_sessions: sessionmaker[Session],
) -> None:
    with postgres_sessions() as setup_session:
        transfer, _ = create_transfer(setup_session, transfer_command("postgres-reject-race"))
        transfer_id = transfer.id

    contender_started = Event()

    def submit_approval() -> DomainError:
        with postgres_sessions() as session:
            contender_started.set()
            return capture_domain_error(
                lambda: record_approval(session, transfer_id, approval("risk.approver"))
            )

    with postgres_sessions() as rejecting_session:
        lock_transfer(rejecting_session, transfer_id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(submit_approval)
            assert contender_started.wait(timeout=5)
            rejected = record_approval(
                rejecting_session,
                transfer_id,
                approval("compliance.approver", decision="reject"),
            )
            error = future.result(timeout=5)

    assert rejected.status == TransferStatus.REJECTED
    assert error.code == "transfer_not_pending"
    with postgres_sessions() as session:
        observed = load_transfer(session, transfer_id)
        assert observed.status == TransferStatus.REJECTED
        assert [(item.approver, item.decision.value) for item in observed.approvals] == [
            ("compliance.approver", "reject")
        ]


def test_only_one_concurrent_approval_can_fill_the_second_slot(
    postgres_sessions: sessionmaker[Session],
) -> None:
    with postgres_sessions() as setup_session:
        transfer, _ = create_transfer(setup_session, transfer_command("postgres-approval-race"))
        transfer_id = transfer.id
        record_approval(setup_session, transfer_id, approval("risk.approver"))

    contender_started = Event()

    def submit_third_approver() -> DomainError:
        with postgres_sessions() as session:
            contender_started.set()
            return capture_domain_error(
                lambda: record_approval(session, transfer_id, approval("operations.approver"))
            )

    with postgres_sessions() as approving_session:
        lock_transfer(approving_session, transfer_id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(submit_third_approver)
            assert contender_started.wait(timeout=5)
            approved = record_approval(approving_session, transfer_id, approval("finance.approver"))
            error = future.result(timeout=5)

    assert approved.status == TransferStatus.APPROVED
    assert error.code == "transfer_not_pending"
    with postgres_sessions() as session:
        observed = load_transfer(session, transfer_id)
        assert observed.status == TransferStatus.APPROVED
        assert {item.approver for item in observed.approvals} == {
            "risk.approver",
            "finance.approver",
        }


def test_different_execution_keys_cannot_race_to_two_attempts(
    postgres_sessions: sessionmaker[Session],
) -> None:
    with postgres_sessions() as setup_session:
        transfer, _ = create_transfer(setup_session, transfer_command("postgres-execution-race"))
        transfer_id = transfer.id
        record_approval(setup_session, transfer_id, approval("risk.approver"))
        record_approval(setup_session, transfer_id, approval("finance.approver"))

    contender_started = Event()

    def submit_other_key() -> DomainError:
        with postgres_sessions() as session:
            contender_started.set()
            return capture_domain_error(
                lambda: execute_transfer(session, transfer_id, "execution-contender")
            )

    with postgres_sessions() as executing_session:
        lock_transfer(executing_session, transfer_id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(submit_other_key)
            assert contender_started.wait(timeout=5)
            completed, created = execute_transfer(
                executing_session, transfer_id, "execution-winner"
            )
            error = future.result(timeout=5)

    assert created is True
    assert completed.status == TransferStatus.COMPLETED
    assert error.code == "transfer_already_executed"
    with postgres_sessions() as session:
        observed = load_transfer(session, transfer_id)
        assert len(observed.executions) == 1
        assert observed.executions[0].idempotency_key == "execution-winner"


def test_database_constraint_rejects_a_second_execution_for_one_transfer(
    postgres_sessions: sessionmaker[Session],
) -> None:
    with postgres_sessions() as session:
        transfer, _ = create_transfer(session, transfer_command("postgres-execution-constraint"))
        session.add(
            ExecutionAttempt(
                transfer_id=transfer.id,
                idempotency_key="constraint-first",
                provider_reference="demo-first",
            )
        )
        session.commit()
        session.add(
            ExecutionAttempt(
                transfer_id=transfer.id,
                idempotency_key="constraint-second",
                provider_reference="demo-second",
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()
