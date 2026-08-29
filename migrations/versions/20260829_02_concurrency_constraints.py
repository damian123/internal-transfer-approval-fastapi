"""Add execution uniqueness and align stored enum values.

Revision ID: 20260829_02
Revises: 20260828_01
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_02"
down_revision: str | None = "20260828_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Earlier application versions persisted Python enum member names. Normalize any
    # existing rows before adding constraints for the public, lower-case wire values.
    op.execute(sa.text("UPDATE transfer_requests SET status = lower(status)"))
    op.execute(sa.text("UPDATE approval_decisions SET decision = lower(decision)"))

    with op.batch_alter_table("transfer_requests") as batch_op:
        batch_op.create_check_constraint(
            "ck_transfer_status_valid",
            "status IN ('submitted', 'approved', 'rejected', 'completed')",
        )

    with op.batch_alter_table("approval_decisions") as batch_op:
        batch_op.create_check_constraint(
            "ck_approval_decision_valid",
            "decision IN ('approve', 'reject')",
        )

    with op.batch_alter_table("execution_attempts") as batch_op:
        batch_op.create_unique_constraint("uq_execution_transfer_id", ["transfer_id"])


def downgrade() -> None:
    with op.batch_alter_table("execution_attempts") as batch_op:
        batch_op.drop_constraint("uq_execution_transfer_id", type_="unique")

    with op.batch_alter_table("approval_decisions") as batch_op:
        batch_op.drop_constraint("ck_approval_decision_valid", type_="check")

    with op.batch_alter_table("transfer_requests") as batch_op:
        batch_op.drop_constraint("ck_transfer_status_valid", type_="check")

    op.execute(sa.text("UPDATE approval_decisions SET decision = upper(decision)"))
    op.execute(sa.text("UPDATE transfer_requests SET status = upper(status)"))
