"""add clinical quality incidents and accreditation benchmarks

Revision ID: a7b8c9d0e1f2
Revises: f6a8b2c4d5e7
Create Date: 2026-10-25 23:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a8b2c4d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create clinical_quality_incidents table
    op.create_table(
        "clinical_quality_incidents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("incident_code", sa.String(length=50), nullable=False),
        sa.Column(
            "incident_type",
            sa.Enum(
                "CAUTI",
                "CLABSI",
                "VAP",
                "SSI",
                "PATIENT_FALL",
                "BEDSORE_HAPU",
                "MEDICATION_ERROR",
                "ADVERSE_DRUG_REACTION",
                "UNPLANNED_ICU_READMISSION",
                "UNPLANNED_OT_RETURN",
                "SENTINEL_EVENT",
                name="clinicalincidenttypeenum",
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum(
                "NEAR_MISS",
                "MINOR",
                "MODERATE",
                "MAJOR",
                "SENTINEL",
                name="clinicalincidentseverityenum",
            ),
            nullable=False,
        ),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("encounter_id", sa.Integer(), sa.ForeignKey("patient_encounters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ward_id", sa.Integer(), sa.ForeignKey("wards.id", ondelete="SET NULL"), nullable=True),
        sa.Column("device_days", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("root_cause_analysis", sa.Text(), nullable=True),
        sa.Column("capa_plan", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "REPORTED",
                "UNDER_REVIEW",
                "CAPA_ASSIGNED",
                "RESOLVED",
                "CLOSED",
                name="clinicalincidentstatusenum",
            ),
            nullable=False,
            server_default="REPORTED",
        ),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_clinical_quality_incidents_incident_code", "clinical_quality_incidents", ["incident_code"], unique=True)
    op.create_index("ix_clinical_quality_incidents_type", "clinical_quality_incidents", ["incident_type"])
    op.create_index("ix_clinical_quality_incidents_severity", "clinical_quality_incidents", ["severity"])
    op.create_index("ix_clinical_quality_incidents_status", "clinical_quality_incidents", ["status"])
    op.create_index("ix_clinical_quality_incidents_reported_at", "clinical_quality_incidents", ["reported_at"])
    op.create_index("ix_clinical_quality_incidents_patient_id", "clinical_quality_incidents", ["patient_id"])
    op.create_index("ix_clinical_quality_incidents_encounter_id", "clinical_quality_incidents", ["encounter_id"])

    # 2. Create quality_indicator_benchmarks table
    benchmarks_table = op.create_table(
        "quality_indicator_benchmarks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("indicator_code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("standard", sa.String(length=50), nullable=False, server_default="NABH_5TH_ED"),
        sa.Column("unit", sa.String(length=50), nullable=False),
        sa.Column("target_threshold", sa.Float(), nullable=False),
        sa.Column("warning_threshold", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_quality_indicator_benchmarks_indicator_code", "quality_indicator_benchmarks", ["indicator_code"], unique=True)

    # 3. Seed NABH / JCI default quality indicator benchmarks
    op.bulk_insert(
        benchmarks_table,
        [
            {
                "indicator_code": "CAUTI_RATE",
                "name": "Catheter-Associated Urinary Tract Infection Rate",
                "standard": "NABH_5TH_ED",
                "unit": "PER_1000_DEVICE_DAYS",
                "target_threshold": 2.0,
                "warning_threshold": 3.5,
                "description": "CAUTI cases per 1,000 urinary catheter days in inpatient wards and ICUs.",
            },
            {
                "indicator_code": "CLABSI_RATE",
                "name": "Central Line-Associated Bloodstream Infection Rate",
                "standard": "NABH_5TH_ED",
                "unit": "PER_1000_DEVICE_DAYS",
                "target_threshold": 1.5,
                "warning_threshold": 3.0,
                "description": "CLABSI cases per 1,000 central venous catheter days.",
            },
            {
                "indicator_code": "VAP_RATE",
                "name": "Ventilator-Associated Pneumonia Rate",
                "standard": "NABH_5TH_ED",
                "unit": "PER_1000_DEVICE_DAYS",
                "target_threshold": 2.0,
                "warning_threshold": 4.0,
                "description": "VAP cases per 1,000 mechanical ventilator days in ICUs.",
            },
            {
                "indicator_code": "SSI_RATE",
                "name": "Surgical Site Infection Rate",
                "standard": "NABH_5TH_ED",
                "unit": "PERCENT",
                "target_threshold": 1.5,
                "warning_threshold": 2.5,
                "description": "Percentage of clean/clean-contaminated surgical procedures developing SSI.",
            },
            {
                "indicator_code": "FALL_RATE",
                "name": "Inpatient Fall Rate",
                "standard": "JCI_7TH_ED",
                "unit": "PER_1000_BED_DAYS",
                "target_threshold": 1.5,
                "warning_threshold": 3.0,
                "description": "Total inpatient falls per 1,000 inpatient bed days.",
            },
            {
                "indicator_code": "MED_ERROR_RATE",
                "name": "Medication Administration Error Rate",
                "standard": "NABH_5TH_ED",
                "unit": "PER_1000_DOSES",
                "target_threshold": 5.0,
                "warning_threshold": 10.0,
                "description": "Medication administration errors and near misses per 1,000 doses administered.",
            },
            {
                "indicator_code": "UNPLANNED_ICU_RETURN_RATE",
                "name": "Unplanned Return to ICU within 48 Hours",
                "standard": "NABH_5TH_ED",
                "unit": "PERCENT",
                "target_threshold": 2.0,
                "warning_threshold": 4.0,
                "description": "Percentage of patients stepped down from ICU returned unexpectedly within 48 hours.",
            },
            {
                "indicator_code": "UNPLANNED_OT_RETURN_RATE",
                "name": "Unplanned Return to Operating Theatre within 48 Hours",
                "standard": "NABH_5TH_ED",
                "unit": "PERCENT",
                "target_threshold": 1.0,
                "warning_threshold": 2.0,
                "description": "Percentage of surgical patients returned to OT for re-exploration within 48 hours.",
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_quality_indicator_benchmarks_indicator_code", table_name="quality_indicator_benchmarks")
    op.drop_table("quality_indicator_benchmarks")
    op.drop_index("ix_clinical_quality_incidents_encounter_id", table_name="clinical_quality_incidents")
    op.drop_index("ix_clinical_quality_incidents_patient_id", table_name="clinical_quality_incidents")
    op.drop_index("ix_clinical_quality_incidents_reported_at", table_name="clinical_quality_incidents")
    op.drop_index("ix_clinical_quality_incidents_status", table_name="clinical_quality_incidents")
    op.drop_index("ix_clinical_quality_incidents_severity", table_name="clinical_quality_incidents")
    op.drop_index("ix_clinical_quality_incidents_type", table_name="clinical_quality_incidents")
    op.drop_index("ix_clinical_quality_incidents_incident_code", table_name="clinical_quality_incidents")
    op.drop_table("clinical_quality_incidents")
