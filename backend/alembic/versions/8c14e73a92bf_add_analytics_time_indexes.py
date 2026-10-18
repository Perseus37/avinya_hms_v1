"""add analytics time-series indexes

Revision ID: 8c14e73a92bf
Revises: 3605df409a44
Create Date: 2026-10-16 22:50:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "8c14e73a92bf"
down_revision: Union[str, None] = "3605df409a44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_invoices_created_at", "invoices", ["created_at"])
    op.create_index(
        "ix_payment_receipts_created_at", "payment_receipts", ["created_at"]
    )
    op.create_index("ix_patients_created_at", "patients", ["created_at"])
    op.create_index(
        "ix_patient_encounters_start_time", "patient_encounters", ["start_time"]
    )
    op.create_index(
        "ix_appointments_appointment_time", "appointments", ["appointment_time"]
    )
    op.create_index(
        "ix_diagnostic_tasks_created_at", "diagnostic_tasks", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_diagnostic_tasks_created_at", table_name="diagnostic_tasks")
    op.drop_index(
        "ix_appointments_appointment_time", table_name="appointments"
    )
    op.drop_index(
        "ix_patient_encounters_start_time", table_name="patient_encounters"
    )
    op.drop_index("ix_patients_created_at", table_name="patients")
    op.drop_index(
        "ix_payment_receipts_created_at", table_name="payment_receipts"
    )
    op.drop_index("ix_invoices_created_at", table_name="invoices")
