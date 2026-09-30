"""add audit actor columns

Revision ID: a4c912de7b31
Revises: 8fdafa8e849a
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4c912de7b31"
down_revision: Union[str, Sequence[str], None] = "8fdafa8e849a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AUDITED_TABLES = (
    "departments",
    "roles",
    "permissions",
    "users",
    "doctor_profiles",
    "user_roles",
    "floors",
    "wards",
    "physical_rooms_master",
    "beds_master",
    "nursing_shift_assignments",
    "chief_complaints_master",
    "comorbidities_master",
    "allergies_master",
    "diagnoses_master",
    "physical_examinations_master",
    "patients",
    "patient_allergies",
    "service_categories",
    "service_catalog",
    "service_bundle_mappings",
    "diagnostic_orders",
    "diagnostic_tasks",
    "patient_encounters",
    "inpatient_admissions",
    "bed_transfer_logs",
)


def upgrade() -> None:
    for table_name in AUDITED_TABLES:
        if table_name in {"doctor_profiles", "user_roles"}:
            op.add_column(
                table_name,
                sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            )
            op.add_column(
                table_name,
                sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            )
        op.add_column(table_name, sa.Column("created_by_id", sa.Integer(), nullable=True))
        op.add_column(table_name, sa.Column("updated_by_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    for table_name in reversed(AUDITED_TABLES):
        op.drop_column(table_name, "updated_by_id")
        op.drop_column(table_name, "created_by_id")
        if table_name in {"doctor_profiles", "user_roles"}:
            op.drop_column(table_name, "updated_at")
            op.drop_column(table_name, "created_at")