"""add named MFA-protected control-plane operators

Revision ID: 1c8b4a72e903
Revises: 95769c3ab4b3
Create Date: 2026-10-19 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1c8b4a72e903"
down_revision: Union[str, None] = "95769c3ab4b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "control_plane_operators",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("mfa_secret", sa.String(length=255), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mfa_last_used_counter", sa.Integer(), nullable=True),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_control_plane_operators_email",
        "control_plane_operators",
        ["email"],
        unique=True,
    )
    op.create_table(
        "control_plane_operator_sessions",
        sa.Column("token_id", sa.String(length=64), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["control_plane_operators.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("token_id"),
    )
    op.create_index(
        "ix_control_plane_operator_sessions_operator_id",
        "control_plane_operator_sessions",
        ["operator_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_control_plane_operator_sessions_operator_id",
        table_name="control_plane_operator_sessions",
    )
    op.drop_table("control_plane_operator_sessions")
    op.drop_index("ix_control_plane_operators_email", table_name="control_plane_operators")
    op.drop_table("control_plane_operators")
