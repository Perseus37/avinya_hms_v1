"""add rcm automated charge capture events table

Revision ID: d4e6f8b0c293
Revises: c3d5e7a9b182
Create Date: 2026-10-25 21:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e6f8b0c293"
down_revision: Union[str, None] = "c3d5e7a9b182"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "charge_capture_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("encounter_id", sa.Integer(), sa.ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_line_item_id", sa.Integer(), sa.ForeignKey("invoice_line_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("source_reference", sa.String(length=100), nullable=False),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("service_catalog.id", ondelete="SET NULL"), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price_paise", sa.Integer(), nullable=False),
        sa.Column("tax_amount_paise", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_charge_paise", sa.Integer(), nullable=False),
        sa.Column("tariff_applied", sa.String(length=100), nullable=False, server_default="BASE_CATALOG"),
        sa.Column("is_insurance_covered", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("source_reference", name="uq_charge_capture_source_ref"),
        sa.CheckConstraint("total_charge_paise >= 0", name="ck_charge_capture_total_non_neg"),
    )
    op.create_index("ix_charge_capture_encounter_id", "charge_capture_events", ["encounter_id"])
    op.create_index("ix_charge_capture_patient_id", "charge_capture_events", ["patient_id"])
    op.create_index("ix_charge_capture_posted_at", "charge_capture_events", ["posted_at"])


def downgrade() -> None:
    op.drop_table("charge_capture_events")
