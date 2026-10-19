"""add policy-neutral privacy revisions and legal holds

Revision ID: 9d82a6b4c1f0
Revises: d6a84c2f91b0
Create Date: 2026-10-20 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9d82a6b4c1f0"
down_revision: Union[str, None] = "d6a84c2f91b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "privacy_policy_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_type", sa.String(length=20), nullable=False),
        sa.Column("policy_document", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column(
            "enforcement_mode",
            sa.String(length=20),
            server_default="OFF",
            nullable=False,
        ),
        sa.Column("configured_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "configured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_reference", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "policy_type IN ('CONSENT', 'RETENTION')",
            name="ck_privacy_policy_revision_type",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED')",
            name="ck_privacy_policy_revision_status",
        ),
        sa.CheckConstraint(
            "enforcement_mode = 'OFF'",
            name="ck_privacy_policy_revision_mode_off",
        ),
        sa.CheckConstraint(
            "(status = 'DRAFT' AND approved_by_user_id IS NULL "
            "AND approved_at IS NULL AND approval_reference IS NULL) OR "
            "(status = 'APPROVED' AND approved_by_user_id IS NOT NULL "
            "AND approved_at IS NOT NULL AND approval_reference IS NOT NULL)",
            name="ck_privacy_policy_revision_approval",
        ),
        sa.ForeignKeyConstraint(["configured_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_privacy_policy_revisions_policy_type",
        "privacy_policy_revisions",
        ["policy_type"],
    )
    op.create_table(
        "privacy_legal_holds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=True),
        sa.Column("data_category", sa.String(length=50), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("hold_reference", sa.String(length=100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("released_by_user_id", sa.Integer(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reference", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "patient_id IS NOT NULL OR data_category IS NOT NULL",
            name="ck_privacy_legal_hold_scope",
        ),
        sa.CheckConstraint(
            "(released_at IS NULL AND released_by_user_id IS NULL "
            "AND release_reference IS NULL) OR "
            "(released_at IS NOT NULL AND released_by_user_id IS NOT NULL "
            "AND release_reference IS NOT NULL)",
            name="ck_privacy_legal_hold_release",
        ),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["released_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_privacy_legal_holds_patient_id",
        "privacy_legal_holds",
        ["patient_id"],
    )
    op.create_index(
        "ix_privacy_legal_holds_data_category",
        "privacy_legal_holds",
        ["data_category"],
    )

    connection = op.get_bind()
    exists = connection.execute(
        sa.text("SELECT 1 FROM permissions WHERE code = :code"),
        {"code": "privacy:approve"},
    ).first()
    if exists is None:
        connection.execute(
            sa.text(
                "INSERT INTO permissions "
                "(code, description, module, created_at, updated_at) "
                "VALUES (:code, :description, :module, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "code": "privacy:approve",
                "description": "Approve privacy policy revisions and release legal holds",
                "module": "Privacy",
            },
        )
    role_id = connection.execute(
        sa.text("SELECT id FROM roles WHERE code = :code"),
        {"code": "ROLE_ADMIN"},
    ).scalar_one_or_none()
    if role_id is not None:
        permission_id = connection.execute(
            sa.text("SELECT id FROM permissions WHERE code = :code"),
            {"code": "privacy:approve"},
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
    permission_id = connection.execute(
        sa.text("SELECT id FROM permissions WHERE code = :code"),
        {"code": "privacy:approve"},
    ).scalar_one_or_none()
    if permission_id is not None:
        connection.execute(
            sa.text("DELETE FROM role_permissions WHERE permission_id = :id"),
            {"id": permission_id},
        )
        connection.execute(
            sa.text("DELETE FROM permissions WHERE id = :id"),
            {"id": permission_id},
        )
    op.drop_index(
        "ix_privacy_legal_holds_data_category",
        table_name="privacy_legal_holds",
    )
    op.drop_index(
        "ix_privacy_legal_holds_patient_id",
        table_name="privacy_legal_holds",
    )
    op.drop_table("privacy_legal_holds")
    op.drop_index(
        "ix_privacy_policy_revisions_policy_type",
        table_name="privacy_policy_revisions",
    )
    op.drop_table("privacy_policy_revisions")
