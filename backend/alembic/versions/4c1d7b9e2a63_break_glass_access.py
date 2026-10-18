"""add audited emergency patient access

Revision ID: 4c1d7b9e2a63
Revises: 2b5c9e14a7d3
Create Date: 2026-10-19 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4c1d7b9e2a63"
down_revision: Union[str, None] = "2b5c9e14a7d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "break_glass_grants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("review_outcome", sa.String(length=20), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_break_glass_grants_patient_id", "break_glass_grants", ["patient_id"])
    op.create_index("ix_break_glass_grants_user_id", "break_glass_grants", ["user_id"])
    op.create_table(
        "break_glass_uses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("grant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("accessed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["grant_id"], ["break_glass_grants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_break_glass_uses_grant_id", "break_glass_uses", ["grant_id"])
    op.create_index("ix_break_glass_uses_user_id", "break_glass_uses", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_break_glass_uses_user_id", table_name="break_glass_uses")
    op.drop_index("ix_break_glass_uses_grant_id", table_name="break_glass_uses")
    op.drop_table("break_glass_uses")
    op.drop_index("ix_break_glass_grants_user_id", table_name="break_glass_grants")
    op.drop_index("ix_break_glass_grants_patient_id", table_name="break_glass_grants")
    op.drop_table("break_glass_grants")
