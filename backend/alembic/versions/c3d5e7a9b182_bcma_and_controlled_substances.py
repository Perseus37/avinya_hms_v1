"""add bcma and controlled substances perpetual vault tables

Revision ID: c3d5e7a9b182
Revises: b2e4c8f1a790
Create Date: 2026-10-25 21:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d5e7a9b182"
down_revision: Union[str, None] = "b2e4c8f1a790"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add controlled substance and barcode columns to medication_master
    op.add_column("medication_master", sa.Column("is_controlled_substance", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.add_column("medication_master", sa.Column("ndc_or_barcode", sa.String(length=100), nullable=True))
    op.create_index("ix_medication_master_ndc_or_barcode", "medication_master", ["ndc_or_barcode"])

    # 2. Create controlled_substance_vaults table
    op.create_table(
        "controlled_substance_vaults",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("medication_id", sa.Integer(), sa.ForeignKey("medication_master.id"), nullable=False, unique=True),
        sa.Column("vault_location", sa.String(length=100), nullable=False, server_default="MAIN_NARCOTICS_SAFE"),
        sa.Column("current_balance_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_of_measure", sa.String(length=30), nullable=False, server_default="AMPOULE"),
        sa.Column("tamper_seal_number", sa.String(length=50), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("current_balance_units >= 0", name="ck_vault_bal_non_neg"),
    )

    # 3. Create controlled_substance_movement_logs table
    op.create_table(
        "controlled_substance_movement_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vault_id", sa.Integer(), sa.ForeignKey("controlled_substance_vaults.id", ondelete="CASCADE"), nullable=False),
        sa.Column("movement_type", sa.String(length=30), nullable=False),
        sa.Column("quantity_units", sa.Integer(), nullable=False),
        sa.Column("balance_before", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("encounter_id", sa.Integer(), sa.ForeignKey("patient_encounters.id"), nullable=True),
        sa.Column("prescription_item_id", sa.Integer(), sa.ForeignKey("prescription_items.id"), nullable=True),
        sa.Column("primary_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("witness_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("witness_verified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("wastage_reason", sa.String(length=255), nullable=True),
        sa.Column("disposal_method", sa.String(length=100), nullable=True),
        sa.Column("batch_number", sa.String(length=50), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("quantity_units > 0", name="ck_narcotic_move_qty_pos"),
        sa.CheckConstraint("balance_after >= 0", name="ck_narcotic_bal_after_non_neg"),
    )
    op.create_index("ix_narcotic_move_vault_id", "controlled_substance_movement_logs", ["vault_id"])
    op.create_index("ix_narcotic_move_created_at", "controlled_substance_movement_logs", ["created_at"])

    # 4. Create bcma_scan_audit_logs table
    op.create_table(
        "bcma_scan_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("encounter_id", sa.Integer(), sa.ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prescription_item_id", sa.Integer(), sa.ForeignKey("prescription_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("administered_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scanned_patient_barcode", sa.String(length=100), nullable=False),
        sa.Column("scanned_medication_barcode", sa.String(length=100), nullable=False),
        sa.Column("patient_matched", sa.Boolean(), nullable=False),
        sa.Column("medication_matched", sa.Boolean(), nullable=False),
        sa.Column("dose_matched", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("time_status", sa.String(length=30), nullable=False, server_default="ON_TIME"),
        sa.Column("verification_status", sa.String(length=50), nullable=False),
        sa.Column("override_reason", sa.String(length=500), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("emar_log_id", sa.Integer(), sa.ForeignKey("medication_administration_logs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_bcma_encounter_time", "bcma_scan_audit_logs", ["encounter_id", "scanned_at"])
    op.create_index("ix_bcma_rx_item", "bcma_scan_audit_logs", ["prescription_item_id"])


def downgrade() -> None:
    op.drop_table("bcma_scan_audit_logs")
    op.drop_table("controlled_substance_movement_logs")
    op.drop_table("controlled_substance_vaults")
    op.drop_index("ix_medication_master_ndc_or_barcode", table_name="medication_master")
    op.drop_column("medication_master", "ndc_or_barcode")
    op.drop_column("medication_master", "is_controlled_substance")
