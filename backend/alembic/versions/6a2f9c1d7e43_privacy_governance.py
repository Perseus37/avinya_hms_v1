"""add consent ledger and versioned retention policies

Revision ID: 6a2f9c1d7e43
Revises: 5d8a1e7c3f42
Create Date: 2026-10-19 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6a2f9c1d7e43"
down_revision: Union[str, None] = "5d8a1e7c3f42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "patient_consent_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("consent_type", sa.String(length=40), nullable=False),
        sa.Column("purpose", sa.String(length=255), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("evidence_reference", sa.String(length=100), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_patient_consent_records_patient_id",
        "patient_consent_records",
        ["patient_id"],
    )
    op.create_table(
        "privacy_retention_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("data_category", sa.String(length=50), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("legal_basis", sa.Text(), nullable=False),
        sa.Column("approval_reference", sa.String(length=100), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("configured_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "configured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["configured_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_privacy_retention_policies_data_category",
        "privacy_retention_policies",
        ["data_category"],
    )

    connection = op.get_bind()
    for code, description, module in (
        ("privacy:read", "View consent records and retention policies", "Privacy"),
        ("privacy:write", "Record consent decisions and retention policies", "Privacy"),
    ):
        exists = connection.execute(
            sa.text("SELECT 1 FROM permissions WHERE code = :code"), {"code": code}
        ).first()
        if exists is None:
            connection.execute(
                sa.text(
                    "INSERT INTO permissions "
                    "(code, description, module, created_at, updated_at) "
                    "VALUES (:code, :description, :module, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"code": code, "description": description, "module": module},
            )
    for role_code, actions in (
        ("ROLE_ADMIN", ("read", "write")),
        ("ROLE_AUDITOR_READONLY", ("read",)),
    ):
        role_id = connection.execute(
            sa.text("SELECT id FROM roles WHERE code = :code"), {"code": role_code}
        ).scalar_one_or_none()
        if role_id is None:
            continue
        for action in actions:
            permission_id = connection.execute(
                sa.text("SELECT id FROM permissions WHERE code = :code"),
                {"code": f"privacy:{action}"},
            ).scalar_one()
            if connection.execute(
                sa.text(
                    "SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id"
                ),
                {"role_id": role_id, "permission_id": permission_id},
            ).first() is None:
                connection.execute(
                    sa.text(
                        "INSERT INTO role_permissions (role_id, permission_id) "
                        "VALUES (:role_id, :permission_id)"
                    ),
                    {"role_id": role_id, "permission_id": permission_id},
                )


def downgrade() -> None:
    connection = op.get_bind()
    for code in ("privacy:read", "privacy:write"):
        permission_id = connection.execute(
            sa.text("SELECT id FROM permissions WHERE code = :code"), {"code": code}
        ).scalar_one_or_none()
        if permission_id is not None:
            connection.execute(
                sa.text("DELETE FROM role_permissions WHERE permission_id = :id"),
                {"id": permission_id},
            )
            connection.execute(
                sa.text("DELETE FROM permissions WHERE id = :id"), {"id": permission_id}
            )
    op.drop_index(
        "ix_privacy_retention_policies_data_category",
        table_name="privacy_retention_policies",
    )
    op.drop_index(
        "ix_patient_consent_records_patient_id",
        table_name="patient_consent_records",
    )
    op.drop_table("privacy_retention_policies")
    op.drop_table("patient_consent_records")
