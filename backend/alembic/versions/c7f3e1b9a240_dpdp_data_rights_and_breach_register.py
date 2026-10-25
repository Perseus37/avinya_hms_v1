"""add dpdp data rights and breach register tables

Revision ID: c7f3e1b9a240
Revises: b5e2d1c9f430
Create Date: 2026-10-22 17:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7f3e1b9a240"
down_revision: Union[str, None] = "b5e2d1c9f430"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. patient_data_rights_requests
    op.create_table(
        "patient_data_rights_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("request_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="SUBMITTED", nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_notes", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("handled_by_user_id", sa.Integer(), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["handled_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_patient_data_rights_requests_created_at",
        "patient_data_rights_requests",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_patient_data_rights_requests_patient_id",
        "patient_data_rights_requests",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        "ix_patient_data_rights_requests_request_type",
        "patient_data_rights_requests",
        ["request_type"],
        unique=False,
    )
    op.create_index(
        "ix_patient_data_rights_requests_status",
        "patient_data_rights_requests",
        ["status"],
        unique=False,
    )

    # 2. privacy_data_breach_incidents
    op.create_table(
        "privacy_data_breach_incidents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("incident_code", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="REPORTED", nullable=False),
        sa.Column("breach_type", sa.String(length=50), nullable=False),
        sa.Column("affected_data_categories", sa.JSON(), nullable=False),
        sa.Column("estimated_affected_subjects", sa.Integer(), server_default="0", nullable=False),
        sa.Column("contained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_to_authority_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_subjects_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("resolution_summary", sa.Text(), nullable=True),
        sa.Column("reported_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["reported_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_privacy_data_breach_incidents_created_at",
        "privacy_data_breach_incidents",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_privacy_data_breach_incidents_incident_code",
        "privacy_data_breach_incidents",
        ["incident_code"],
        unique=True,
    )
    op.create_index(
        "ix_privacy_data_breach_incidents_severity",
        "privacy_data_breach_incidents",
        ["severity"],
        unique=False,
    )
    op.create_index(
        "ix_privacy_data_breach_incidents_status",
        "privacy_data_breach_incidents",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_privacy_data_breach_incidents_status", table_name="privacy_data_breach_incidents")
    op.drop_index("ix_privacy_data_breach_incidents_severity", table_name="privacy_data_breach_incidents")
    op.drop_index("ix_privacy_data_breach_incidents_incident_code", table_name="privacy_data_breach_incidents")
    op.drop_index("ix_privacy_data_breach_incidents_created_at", table_name="privacy_data_breach_incidents")
    op.drop_table("privacy_data_breach_incidents")

    op.drop_index("ix_patient_data_rights_requests_status", table_name="patient_data_rights_requests")
    op.drop_index("ix_patient_data_rights_requests_request_type", table_name="patient_data_rights_requests")
    op.drop_index("ix_patient_data_rights_requests_patient_id", table_name="patient_data_rights_requests")
    op.drop_index("ix_patient_data_rights_requests_created_at", table_name="patient_data_rights_requests")
    op.drop_table("patient_data_rights_requests")

