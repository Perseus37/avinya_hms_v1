"""Add one-time email proof for privileged MFA enrollment.

Revision ID: 8a4f2d7c1e69
Revises: 7c4e2a9b6d10
Create Date: 2026-10-19 12:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a4f2d7c1e69"
down_revision: Union[str, None] = "7c4e2a9b6d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mfa_enrollment_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_mfa_enrollment_codes_user_id"),
        "mfa_enrollment_codes",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_mfa_enrollment_codes_user_id"), table_name="mfa_enrollment_codes"
    )
    op.drop_table("mfa_enrollment_codes")
