"""custom inventory alert policies

Revision ID: d19f6b3a4c82
Revises: c74e11ab42d9
Create Date: 2026-10-17 21:50:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d19f6b3a4c82"
down_revision: Union[str, None] = "c74e11ab42d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hospital_policy_settings",
        sa.Column(
            "inventory_alert_detail_limit",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
    )
    op.add_column(
        "medication_master",
        sa.Column("low_stock_threshold_units", sa.Integer(), nullable=True),
    )
    op.add_column(
        "medication_master",
        sa.Column("fefo_expiry_warning_days", sa.Integer(), nullable=True),
    )
def downgrade() -> None:
    op.drop_column("medication_master", "fefo_expiry_warning_days")
    op.drop_column("medication_master", "low_stock_threshold_units")
    op.drop_column("hospital_policy_settings", "inventory_alert_detail_limit")
