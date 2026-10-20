"""add validated policy activation and privacy decision audit

Revision ID: a4f2c8d1e639
Revises: 9d82a6b4c1f0
Create Date: 2026-10-20 09:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4f2c8d1e639"
down_revision: Union[str, None] = "9d82a6b4c1f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("privacy_policy_revisions") as batch_op:
        batch_op.drop_constraint(
            "ck_privacy_policy_revision_mode_off", type_="check"
        )
        batch_op.add_column(
            sa.Column(
                "is_active",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("mode_set_by_user_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("mode_set_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("mode_approval_reference", sa.String(length=100), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_privacy_policy_revisions_mode_set_by_user_id_users",
            "users",
            ["mode_set_by_user_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            "ck_privacy_policy_revision_mode",
            "enforcement_mode IN ('OFF', 'AUDIT_ONLY')",
        )
        batch_op.create_check_constraint(
            "ck_privacy_policy_revision_activation",
            "(is_active = false AND enforcement_mode = 'OFF') OR "
            "(is_active = true AND status = 'APPROVED' "
            "AND enforcement_mode = 'AUDIT_ONLY' "
            "AND mode_set_by_user_id IS NOT NULL "
            "AND mode_set_at IS NOT NULL "
            "AND mode_approval_reference IS NOT NULL)",
        )
    op.create_index(
        "uq_privacy_policy_revision_active",
        "privacy_policy_revisions",
        ["policy_type"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active IS TRUE"),
    )

    with op.batch_alter_table("patient_consent_records") as batch_op:
        batch_op.add_column(
            sa.Column(
                "scope_type",
                sa.String(length=20),
                server_default="patient",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("scope_key", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column(
                "consenter_type",
                sa.String(length=20),
                server_default="patient",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("policy_revision_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("form_version", sa.String(length=100), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_consent_policy_revision",
            "privacy_policy_revisions",
            ["policy_revision_id"],
            ["id"],
        )

    op.create_table(
        "privacy_decision_audits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_revision_id", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("data_use_point", sa.String(length=120), nullable=False),
        sa.Column("purpose_code", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["policy_revision_id"], ["privacy_policy_revisions.id"]
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_privacy_decision_audits_data_use_point",
        "privacy_decision_audits",
        ["data_use_point"],
    )
    op.create_index(
        "ix_privacy_decision_audits_occurred_at",
        "privacy_decision_audits",
        ["occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "uq_privacy_policy_revision_active",
        table_name="privacy_policy_revisions",
    )
    op.drop_index(
        "ix_privacy_decision_audits_occurred_at",
        table_name="privacy_decision_audits",
    )
    op.drop_index(
        "ix_privacy_decision_audits_data_use_point",
        table_name="privacy_decision_audits",
    )
    op.drop_table("privacy_decision_audits")
    with op.batch_alter_table("patient_consent_records") as batch_op:
        batch_op.drop_constraint(
            "fk_consent_policy_revision",
            type_="foreignkey",
        )
        batch_op.drop_column("form_version")
        batch_op.drop_column("policy_revision_id")
        batch_op.drop_column("consenter_type")
        batch_op.drop_column("scope_key")
        batch_op.drop_column("scope_type")
    with op.batch_alter_table("privacy_policy_revisions") as batch_op:
        batch_op.drop_constraint(
            "ck_privacy_policy_revision_activation", type_="check"
        )
        batch_op.drop_constraint("ck_privacy_policy_revision_mode", type_="check")
        batch_op.drop_constraint(
            "fk_privacy_policy_revisions_mode_set_by_user_id_users",
            type_="foreignkey",
        )
        batch_op.drop_column("mode_approval_reference")
        batch_op.drop_column("mode_set_at")
        batch_op.drop_column("mode_set_by_user_id")
        batch_op.drop_column("is_active")
        batch_op.create_check_constraint(
            "ck_privacy_policy_revision_mode_off", "enforcement_mode = 'OFF'"
        )
