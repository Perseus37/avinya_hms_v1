"""add abdm and nhcx gateway tables

Revision ID: e5f7a9c1b384
Revises: d4e6f8b0c293
Create Date: 2026-10-25 22:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f7a9c1b384"
down_revision: Union[str, None] = "d4e6f8b0c293"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add abha_address to patients table
    op.add_column("patients", sa.Column("abha_address", sa.String(length=100), nullable=True))
    op.create_index("ix_patients_abha_address", "patients", ["abha_address"], unique=True)

    # 2. Create abdm_consent_artefacts table
    op.create_table(
        "abdm_consent_artefacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("consent_id", sa.String(length=100), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hiu_id", sa.String(length=100), nullable=False),
        sa.Column("hip_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="GRANTED"),
        sa.Column("hi_types", sa.String(length=255), nullable=False),
        sa.Column("date_range_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_range_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expiry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_manager_id", sa.String(length=100), nullable=False, server_default="sbx"),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("consent_id", name="uq_abdm_consent_id"),
    )
    op.create_index("ix_abdm_consent_artefacts_consent_id", "abdm_consent_artefacts", ["consent_id"], unique=True)
    op.create_index("ix_abdm_consent_patient_id", "abdm_consent_artefacts", ["patient_id"])
    op.create_index("ix_abdm_consent_status", "abdm_consent_artefacts", ["status"])
    op.create_index("ix_abdm_consent_expiry", "abdm_consent_artefacts", ["expiry_time"])

    # 3. Create nhcx_claim_exchange_logs table
    op.create_table(
        "nhcx_claim_exchange_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("transaction_id", sa.String(length=100), nullable=False),
        sa.Column("encounter_id", sa.Integer(), sa.ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("insurance_claims.id", ondelete="SET NULL"), nullable=True),
        sa.Column("preauth_id", sa.Integer(), sa.ForeignKey("insurance_pre_auths.id", ondelete="SET NULL"), nullable=True),
        sa.Column("exchange_type", sa.String(length=50), nullable=False),
        sa.Column("nhcx_status", sa.String(length=50), nullable=False, server_default="SUBMITTED"),
        sa.Column("bundle_json", sa.Text(), nullable=False),
        sa.Column("response_bundle_json", sa.Text(), nullable=True),
        sa.Column("adjudicated_amount_paise", sa.Integer(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("transaction_id", name="uq_nhcx_txn_id"),
    )
    op.create_index("ix_nhcx_claim_exchange_logs_transaction_id", "nhcx_claim_exchange_logs", ["transaction_id"], unique=True)
    op.create_index("ix_nhcx_encounter_id", "nhcx_claim_exchange_logs", ["encounter_id"])
    op.create_index("ix_nhcx_claim_id", "nhcx_claim_exchange_logs", ["claim_id"])
    op.create_index("ix_nhcx_preauth_id", "nhcx_claim_exchange_logs", ["preauth_id"])
    op.create_index("ix_nhcx_posted_at", "nhcx_claim_exchange_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("nhcx_claim_exchange_logs")
    op.drop_table("abdm_consent_artefacts")
    op.drop_index("ix_patients_abha_address", table_name="patients")
    op.drop_column("patients", "abha_address")
