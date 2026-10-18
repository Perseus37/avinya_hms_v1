"""appointment status history

Revision ID: 82ca74d1b605
Revises: 7b2d4c9e1f63
Create Date: 2026-10-18 01:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "82ca74d1b605"
down_revision: Union[str, None] = "7b2d4c9e1f63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "appointment_status_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("changed_by_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["appointment_id"], ["appointments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_appointment_status_history_appointment_time",
        "appointment_status_history",
        ["appointment_id", "changed_at"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO appointment_status_history (
            appointment_id, from_status, to_status, changed_at, changed_by_id,
            reason
        )
        SELECT id, NULL, status, created_at, created_by_id,
               'Status at migration; prior transitions unavailable'
        FROM appointments
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_appointment_status_history_appointment_time",
        table_name="appointment_status_history",
    )
    op.drop_table("appointment_status_history")
