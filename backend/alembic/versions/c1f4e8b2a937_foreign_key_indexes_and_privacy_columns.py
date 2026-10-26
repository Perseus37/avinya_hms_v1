"""foreign key indexes and privacy columns widening

Revision ID: c1f4e8b2a937
Revises: a7b8c9d0e1f2
Create Date: 2026-10-26 14:40:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c1f4e8b2a937"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. N-14, N-26: Widen evidence_reference and reason_code to String(255)
    with op.batch_alter_table("patient_consent_records") as batch_op:
        batch_op.alter_column(
            "evidence_reference",
            type_=sa.String(length=255),
            existing_type=sa.String(length=100),
            existing_nullable=True,
        )
    with op.batch_alter_table("privacy_decision_audits") as batch_op:
        batch_op.alter_column(
            "reason_code",
            type_=sa.String(length=255),
            existing_type=sa.String(length=100),
            existing_nullable=True,
        )

    # 2. N-51: Foreign key indexes on hot operational and retention paths
    op.create_index(
        "ix_diagnostic_orders_patient_id",
        "diagnostic_orders",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        "ix_diagnostic_orders_ordering_doctor_id",
        "diagnostic_orders",
        ["ordering_doctor_id"],
        unique=False,
    )
    op.create_index(
        "ix_diagnostic_tasks_order_id",
        "diagnostic_tasks",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        "ix_diagnostic_tasks_service_id",
        "diagnostic_tasks",
        ["service_id"],
        unique=False,
    )
    op.create_index(
        "ix_prescriptions_encounter_id",
        "prescriptions",
        ["encounter_id"],
        unique=False,
    )
    op.create_index(
        "ix_prescriptions_prescribing_doctor_id",
        "prescriptions",
        ["prescribing_doctor_id"],
        unique=False,
    )
    op.create_index(
        "ix_prescription_items_prescription_id",
        "prescription_items",
        ["prescription_id"],
        unique=False,
    )
    op.create_index(
        "ix_prescription_items_medication_id",
        "prescription_items",
        ["medication_id"],
        unique=False,
    )
    op.create_index(
        "ix_patient_encounters_department_id",
        "patient_encounters",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_patient_encounters_primary_doctor_id",
        "patient_encounters",
        ["primary_doctor_id"],
        unique=False,
    )
    op.create_index(
        "ix_invoice_line_items_service_id",
        "invoice_line_items",
        ["service_id"],
        unique=False,
    )
    op.create_index(
        "ix_patient_consent_records_recorded_by_user_id",
        "patient_consent_records",
        ["recorded_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_patient_allergies_allergy_id",
        "patient_allergies",
        ["allergy_id"],
        unique=False,
    )
    op.create_index(
        "ix_patient_allergies_recorded_by_user_id",
        "patient_allergies",
        ["recorded_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    # 2. Drop foreign key indexes
    op.drop_index("ix_patient_allergies_recorded_by_user_id", table_name="patient_allergies")
    op.drop_index("ix_patient_allergies_allergy_id", table_name="patient_allergies")
    op.drop_index("ix_patient_consent_records_recorded_by_user_id", table_name="patient_consent_records")
    op.drop_index("ix_invoice_line_items_service_id", table_name="invoice_line_items")
    op.drop_index("ix_patient_encounters_primary_doctor_id", table_name="patient_encounters")
    op.drop_index("ix_patient_encounters_department_id", table_name="patient_encounters")
    op.drop_index("ix_prescription_items_medication_id", table_name="prescription_items")
    op.drop_index("ix_prescription_items_prescription_id", table_name="prescription_items")
    op.drop_index("ix_prescriptions_prescribing_doctor_id", table_name="prescriptions")
    op.drop_index("ix_prescriptions_encounter_id", table_name="prescriptions")
    op.drop_index("ix_diagnostic_tasks_service_id", table_name="diagnostic_tasks")
    op.drop_index("ix_diagnostic_tasks_order_id", table_name="diagnostic_tasks")
    op.drop_index("ix_diagnostic_orders_ordering_doctor_id", table_name="diagnostic_orders")
    op.drop_index("ix_diagnostic_orders_patient_id", table_name="diagnostic_orders")

    # 1. Narrow evidence_reference and reason_code back to String(100)
    with op.batch_alter_table("privacy_decision_audits") as batch_op:
        batch_op.alter_column(
            "reason_code",
            type_=sa.String(length=100),
            existing_type=sa.String(length=255),
            existing_nullable=True,
        )
    with op.batch_alter_table("patient_consent_records") as batch_op:
        batch_op.alter_column(
            "evidence_reference",
            type_=sa.String(length=100),
            existing_type=sa.String(length=255),
            existing_nullable=True,
        )
