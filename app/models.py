from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


class TransferStatus(StrEnum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"


class ApprovalDecisionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class TransferRequest(Base):
    __tablename__ = "transfer_requests"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transfer_amount_positive"),
        UniqueConstraint("client_request_id", name="uq_transfer_client_request_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    client_request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    requester: Mapped[str] = mapped_column(String(120), nullable=False)
    source_account: Mapped[str] = mapped_column(String(120), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[TransferStatus] = mapped_column(
        Enum(TransferStatus, native_enum=False), default=TransferStatus.SUBMITTED, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )

    approvals: Mapped[list["ApprovalDecision"]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan"
    )
    executions: Mapped[list["ExecutionAttempt"]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan"
    )


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint("transfer_id", "approver", name="uq_approval_transfer_approver"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    transfer_id: Mapped[UUID] = mapped_column(
        ForeignKey("transfer_requests.id", ondelete="CASCADE"), nullable=False
    )
    approver: Mapped[str] = mapped_column(String(120), nullable=False)
    decision: Mapped[ApprovalDecisionType] = mapped_column(
        Enum(ApprovalDecisionType, native_enum=False), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    transfer: Mapped[TransferRequest] = relationship(back_populates="approvals")


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempts"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_execution_idempotency_key"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    transfer_id: Mapped[UUID] = mapped_column(
        ForeignKey("transfer_requests.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    transfer: Mapped[TransferRequest] = relationship(back_populates="executions")
