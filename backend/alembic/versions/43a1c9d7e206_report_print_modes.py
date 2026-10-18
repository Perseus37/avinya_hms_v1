"""report print modes

Revision ID: 43a1c9d7e206
Revises: d19f6b3a4c82
Create Date: 2026-10-18 00:20:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "43a1c9d7e206"
down_revision: Union[str, None] = "d19f6b3a4c82"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "report_templates",
        sa.Column(
            "print_mode",
            sa.String(length=32),
            nullable=False,
            server_default="DESIGNED",
        ),
    )
    op.add_column(
        "hospital_policy_settings",
        sa.Column(
            "report_print_modes_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("hospital_policy_settings", "report_print_modes_json")
    op.drop_column("report_templates", "print_mode")
