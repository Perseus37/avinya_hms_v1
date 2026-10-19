"""store encrypted tenant-specific HL7 listener credentials

Revision ID: 3f6a8c1d9b24
Revises: 1c8b4a72e903
Create Date: 2026-10-19 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3f6a8c1d9b24"
down_revision: Union[str, None] = "1c8b4a72e903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_registry",
        sa.Column("hl7_shared_secret_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_registry", "hl7_shared_secret_encrypted")
