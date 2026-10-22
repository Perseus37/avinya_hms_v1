"""add granular role to control plane operators

Revision ID: 4d7e9b2c1a85
Revises: 3f6a8c1d9b24
Create Date: 2026-10-22 15:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d7e9b2c1a85"
down_revision: Union[str, None] = "3f6a8c1d9b24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "control_plane_operators",
        sa.Column("role", sa.String(length=50), nullable=False, server_default="superadmin"),
    )


def downgrade() -> None:
    op.drop_column("control_plane_operators", "role")

