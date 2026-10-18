"""appointment waitlist

Revision ID: a61f3e0c8d24
Revises: 94e7c2a81b36
Create Date: 2026-10-18 01:45:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a61f3e0c8d24"
down_revision: Union[str, None] = "94e7c2a81b36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "appointment_waitlist_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("doctor_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("requested_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason_for_visit", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "WAITING",
                "BOOKED",
                "CANCELLED",
                "EXPIRED",
                name="appointmentwaitliststatusenum",
            ),
            server_default="WAITING",
            nullable=False,
        ),
        sa.Column("appointment_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "requested_end_at > requested_start_at",
            name="ck_appointment_waitlist_valid_window",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"], ["appointments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"]
        ),
        sa.ForeignKeyConstraint(["doctor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("appointment_id"),
    )
    op.create_index(
        "ix_appointment_waitlist_doctor_window_status",
        "appointment_waitlist_entries",
        ["doctor_id", "requested_start_at", "status"],
        unique=False,
    )
    op.create_index(
        "ix_appointment_waitlist_patient_status",
        "appointment_waitlist_entries",
        ["patient_id", "status"],
        unique=False,
    )
    op.create_table(
        "appointment_waitlist_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("waitlist_entry_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("changed_by_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["changed_by_id"], ["users.id"]
        ),
        sa.ForeignKeyConstraint(
            ["waitlist_entry_id"],
            ["appointment_waitlist_entries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_appointment_waitlist_history_entry_time",
        "appointment_waitlist_history",
        ["waitlist_entry_id", "changed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_appointment_waitlist_history_entry_time",
        table_name="appointment_waitlist_history",
    )
    op.drop_table("appointment_waitlist_history")
    op.drop_index(
        "ix_appointment_waitlist_patient_status",
        table_name="appointment_waitlist_entries",
    )
    op.drop_index(
        "ix_appointment_waitlist_doctor_window_status",
        table_name="appointment_waitlist_entries",
    )
    op.drop_table("appointment_waitlist_entries")
