"""appointment planned duration snapshot

Revision ID: b72c6e4a109f
Revises: a61f3e0c8d24
Create Date: 2026-10-18 02:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b72c6e4a109f"
down_revision: Union[str, None] = "a61f3e0c8d24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("planned_duration_minutes", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("appointments", "planned_duration_minutes")
