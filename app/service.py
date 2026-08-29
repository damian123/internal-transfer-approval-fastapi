from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ApprovalDecision,
    ApprovalDecisionType,
    ExecutionAttempt,
    TransferRequest,
    TransferStatus,
)
from app.schemas import ApprovalCreate, TransferCreate


@dataclass
class DomainError(Exception):
    code: str
    message: str
    status_code: int


def load_transfer(
    session: Session, transfer_id: UUID, *, for_update: bool = False
) -> TransferRequest:
    query = (
        select(TransferRequest)
        .options(
            selectinload(TransferRequest.approvals),
            selectinload(TransferRequest.executions),
        )
        .where(TransferRequest.id == transfer_id)
    )
    if for_update:
        # PostgreSQL serializes all state transitions for one transfer on this row.
        # SQLite ignores FOR UPDATE, which is sufficient for the single-process smoke tests.
        query = query.with_for_update()
    transfer = session.scalar(query)
    if transfer is None:
        raise DomainError("transfer_not_found", "Transfer request was not found", 404)
    return transfer


def ensure_matching_transfer(existing: TransferRequest, command: TransferCreate) -> None:
    submitted = command.model_dump()
    if any(getattr(existing, field) != value for field, value in submitted.items()):
        raise DomainError(
            "idempotency_payload_mismatch",
            "The client request ID is already associated with different transfer data",
            409,
        )


def find_execution(session: Session, idempotency_key: str) -> ExecutionAttempt | None:
    return session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.idempotency_key == idempotency_key)
    )


def resolve_execution_replay(
    session: Session,
    previous: ExecutionAttempt,
    transfer_id: UUID,
) -> tuple[TransferRequest, bool]:
    if previous.transfer_id != transfer_id:
        raise DomainError(
            "idempotency_key_reused",
            "The idempotency key is already associated with another transfer",
            409,
        )
    return load_transfer(session, transfer_id), False


def create_transfer(session: Session, command: TransferCreate) -> tuple[TransferRequest, bool]:
    existing = session.scalar(
        select(TransferRequest).where(
            TransferRequest.client_request_id == command.client_request_id
        )
    )
    if existing is not None:
        ensure_matching_transfer(existing, command)
        return load_transfer(session, existing.id), False

    transfer = TransferRequest(**command.model_dump())
    session.add(transfer)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        concurrent = session.scalar(
            select(TransferRequest).where(
                TransferRequest.client_request_id == command.client_request_id
            )
        )
        if concurrent is None:
            raise
        # The uniqueness race is only an idempotent replay when every submitted field
        # matches. A conflicting payload must receive the same 409 as a sequential reuse.
        ensure_matching_transfer(concurrent, command)
        return load_transfer(session, concurrent.id), False
    return load_transfer(session, transfer.id), True


def record_approval(
    session: Session, transfer_id: UUID, command: ApprovalCreate
) -> TransferRequest:
    transfer = load_transfer(session, transfer_id, for_update=True)
    if transfer.status != TransferStatus.SUBMITTED:
        session.rollback()
        raise DomainError(
            "transfer_not_pending", "Only submitted transfers may receive decisions", 409
        )
    if command.approver == transfer.requester:
        session.rollback()
        raise DomainError(
            "separation_of_duties", "The requester may not approve their own transfer", 409
        )
    if any(decision.approver == command.approver for decision in transfer.approvals):
        session.rollback()
        raise DomainError(
            "duplicate_approver", "An approver may decide on a transfer only once", 409
        )

    decision = ApprovalDecision(transfer_id=transfer.id, **command.model_dump())
    transfer.approvals.append(decision)
    if command.decision == ApprovalDecisionType.REJECT:
        transfer.status = TransferStatus.REJECTED
    else:
        approvals = sum(
            item.decision == ApprovalDecisionType.APPROVE for item in transfer.approvals
        )
        if approvals >= 2:
            transfer.status = TransferStatus.APPROVED

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DomainError(
            "duplicate_approver", "An approver may decide on a transfer only once", 409
        ) from exc
    return load_transfer(session, transfer.id)


def execute_transfer(
    session: Session, transfer_id: UUID, idempotency_key: str
) -> tuple[TransferRequest, bool]:
    previous = find_execution(session, idempotency_key)
    if previous is not None:
        return resolve_execution_replay(session, previous, transfer_id)

    transfer = load_transfer(session, transfer_id, for_update=True)

    # A same-key request may have waited behind the transfer row lock. Recheck after
    # acquiring the lock so it is reported as a replay instead of a state conflict.
    previous = find_execution(session, idempotency_key)
    if previous is not None:
        return resolve_execution_replay(session, previous, transfer_id)

    if transfer.status == TransferStatus.COMPLETED:
        session.rollback()
        raise DomainError(
            "transfer_already_executed",
            "The transfer already has an execution recorded under another idempotency key",
            409,
        )
    if transfer.status != TransferStatus.APPROVED:
        session.rollback()
        raise DomainError("transfer_not_approved", "Two independent approvals are required", 409)

    attempt = ExecutionAttempt(
        transfer_id=transfer.id,
        idempotency_key=idempotency_key,
        provider_reference=f"demo-{uuid4()}",
    )
    transfer.executions.append(attempt)
    transfer.status = TransferStatus.COMPLETED
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        previous = find_execution(session, idempotency_key)
        if previous is not None:
            return resolve_execution_replay(session, previous, transfer_id)

        concurrent = session.scalar(
            select(ExecutionAttempt).where(ExecutionAttempt.transfer_id == transfer_id)
        )
        if concurrent is not None:
            raise DomainError(
                "transfer_already_executed",
                "The transfer already has an execution recorded under another idempotency key",
                409,
            ) from exc
        raise
    return load_transfer(session, transfer.id), True
