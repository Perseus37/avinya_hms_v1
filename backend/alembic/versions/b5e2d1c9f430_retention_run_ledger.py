"""add retention run ledger

Revision ID: b5e2d1c9f430
Revises: a4f2c8d1e639
Create Date: 2026-10-22 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5e2d1c9f430"
down_revision: Union[str, None] = "a4f2c8d1e639"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "privacy_retention_run_ledgers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_revision_id", sa.Integer(), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("held_count", sa.Integer(), nullable=False),
        sa.Column("affected_count", sa.Integer(), nullable=False),
        sa.Column("approval_reference", sa.String(length=100), nullable=False),
        sa.Column("executed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["policy_revision_id"], ["privacy_policy_revisions.id"]),
        sa.ForeignKeyConstraint(["executed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_privacy_retention_run_ledgers_executed_at",
        "privacy_retention_run_ledgers",
        ["executed_at"],
        unique=False,
    )
    op.create_index(
        "ix_privacy_retention_run_ledgers_policy_revision_id",
        "privacy_retention_run_ledgers",
        ["policy_revision_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_privacy_retention_run_ledgers_policy_revision_id", table_name="privacy_retention_run_ledgers")
    op.drop_index("ix_privacy_retention_run_ledgers_executed_at", table_name="privacy_retention_run_ledgers")
    op.drop_table("privacy_retention_run_ledgers")
