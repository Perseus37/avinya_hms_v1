"""add structured patient postal address fields

Revision ID: e31c7a9b2d40
Revises: b72c6e4a109f
Create Date: 2026-10-18 12:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e31c7a9b2d40"
down_revision: Union[str, None] = "b72c6e4a109f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("patients", sa.Column("pincode", sa.String(length=6), nullable=True))
    op.add_column("patients", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("patients", sa.Column("district", sa.String(length=100), nullable=True))
    op.add_column("patients", sa.Column("state", sa.String(length=100), nullable=True))
    op.add_column("patients", sa.Column("village", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("patients", "village")
    op.drop_column("patients", "state")
    op.drop_column("patients", "district")
    op.drop_column("patients", "city")
    op.drop_column("patients", "pincode")
