"""add automatic expiry for password-failure account lockouts

Revision ID: f7a31c5d9e42
Revises: e31c7a9b2d40
Create Date: 2026-10-19 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a31c5d9e42"
down_revision: Union[str, None] = "e31c7a9b2d40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "locked_until")
