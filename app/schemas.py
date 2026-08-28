from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import ApprovalDecisionType, TransferStatus

Actor = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
Account = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
Currency = Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Z]{3}$")]


class TransferCreate(BaseModel):
    client_request_id: Annotated[str, StringConstraints(min_length=3, max_length=100)]
    requester: Actor
    source_account: Account
    destination_account: Account
    currency: Currency
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=8)
    purpose: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class ApprovalCreate(BaseModel):
    approver: Actor
    decision: ApprovalDecisionType
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    approver: str
    decision: ApprovalDecisionType
    reason: str
    decided_at: datetime


class ExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    idempotency_key: str
    provider_reference: str
    created_at: datetime


class TransferRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    client_request_id: str
    requester: str
    source_account: str
    destination_account: str
    currency: str
    amount: Decimal
    purpose: str
    status: TransferStatus
    created_at: datetime
    updated_at: datetime
    approvals: list[ApprovalRead]
    executions: list[ExecutionRead]


class ErrorRead(BaseModel):
    code: str
    message: str
