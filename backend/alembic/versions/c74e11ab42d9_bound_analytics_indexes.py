"""bound analytics time windows

Revision ID: c74e11ab42d9
Revises: 8c14e73a92bf
Create Date: 2026-10-16 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "c74e11ab42d9"
down_revision: Union[str, None] = "8c14e73a92bf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_encounter_vitals_created_at", "encounter_vitals", ["created_at"]
    )
    op.create_index(
        "ix_encounter_diagnoses_created_at",
        "encounter_diagnoses",
        ["created_at"],
    )
    op.create_index(
        "ix_fluid_balance_logs_recorded_at", "fluid_balance_logs", ["recorded_at"]
    )
    op.create_index(
        "ix_insurance_pre_auths_created_at",
        "insurance_pre_auths",
        ["created_at"],
    )
    op.create_index(
        "ix_insurance_claims_created_at", "insurance_claims", ["created_at"]
    )
    op.create_index(
        "ix_patient_deposits_created_at", "patient_deposits", ["created_at"]
    )
    op.create_index(
        "ix_refund_vouchers_created_at", "refund_vouchers", ["created_at"]
    )
    op.create_index(
        "ix_prescriptions_created_at", "prescriptions", ["created_at"]
    )
    op.create_index(
        "ix_prescription_items_created_at",
        "prescription_items",
        ["created_at"],
    )
    op.create_index(
        "ix_diagnostic_orders_created_at", "diagnostic_orders", ["created_at"]
    )
    op.create_index(
        "ix_surgical_schedules_actual_start_time",
        "surgical_schedules",
        ["actual_start_time"],
    )
    op.create_index(
        "ix_inpatient_admissions_discharge_time",
        "inpatient_admissions",
        ["discharge_time"],
    )
    op.create_index(
        "ix_bed_transfer_logs_transfer_time",
        "bed_transfer_logs",
        ["transfer_time"],
    )
    op.create_index(
        "ix_er_triage_assessments_created_at",
        "er_triage_assessments",
        ["created_at"],
    )
    op.create_index(
        "ix_medico_legal_cases_created_at",
        "medico_legal_cases",
        ["created_at"],
    )
    op.create_index(
        "ix_medication_admin_logs_administered_at",
        "medication_administration_logs",
        ["administered_at"],
    )
    op.create_index(
        "ix_surgical_schedules_created_at",
        "surgical_schedules",
        ["created_at"],
    )
    op.create_index(
        "ix_operative_anesthesia_records_created_at",
        "operative_anesthesia_records",
        ["created_at"],
    )
    op.create_index(
        "ix_surgical_implant_logs_created_at",
        "surgical_implant_logs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_surgical_implant_logs_created_at",
        table_name="surgical_implant_logs",
    )
    op.drop_index(
        "ix_operative_anesthesia_records_created_at",
        table_name="operative_anesthesia_records",
    )
    op.drop_index(
        "ix_surgical_schedules_created_at",
        table_name="surgical_schedules",
    )
    op.drop_index(
        "ix_medication_admin_logs_administered_at",
        table_name="medication_administration_logs",
    )
    op.drop_index(
        "ix_medico_legal_cases_created_at",
        table_name="medico_legal_cases",
    )
    op.drop_index(
        "ix_er_triage_assessments_created_at",
        table_name="er_triage_assessments",
    )
    op.drop_index(
        "ix_bed_transfer_logs_transfer_time",
        table_name="bed_transfer_logs",
    )
    op.drop_index(
        "ix_inpatient_admissions_discharge_time",
        table_name="inpatient_admissions",
    )
    op.drop_index(
        "ix_surgical_schedules_actual_start_time",
        table_name="surgical_schedules",
    )
    op.drop_index(
        "ix_diagnostic_orders_created_at", table_name="diagnostic_orders"
    )
    op.drop_index(
        "ix_prescription_items_created_at", table_name="prescription_items"
    )
    op.drop_index(
        "ix_prescriptions_created_at", table_name="prescriptions"
    )
    op.drop_index(
        "ix_refund_vouchers_created_at", table_name="refund_vouchers"
    )
    op.drop_index(
        "ix_patient_deposits_created_at", table_name="patient_deposits"
    )
    op.drop_index(
        "ix_insurance_claims_created_at", table_name="insurance_claims"
    )
    op.drop_index(
        "ix_insurance_pre_auths_created_at", table_name="insurance_pre_auths"
    )
    op.drop_index(
        "ix_fluid_balance_logs_recorded_at", table_name="fluid_balance_logs"
    )
    op.drop_index(
        "ix_encounter_diagnoses_created_at",
        table_name="encounter_diagnoses",
    )
    op.drop_index(
        "ix_encounter_vitals_created_at", table_name="encounter_vitals"
    )
