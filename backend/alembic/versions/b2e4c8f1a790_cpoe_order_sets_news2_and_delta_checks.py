"""add cpoe order sets news2 deterioration and lis delta check tables

Revision ID: b2e4c8f1a790
Revises: a1c9e8f4b302
Create Date: 2026-10-25 20:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2e4c8f1a790"
down_revision: Union[str, None] = "a1c9e8f4b302"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create order_set_masters table
    op.create_table(
        "order_set_masters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("specialty", sa.String(length=100), nullable=False),
        sa.Column("clinical_indication", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_order_set_masters_code", "order_set_masters", ["code"], unique=True)

    # 2. Create order_set_items table
    op.create_table(
        "order_set_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_set_id", sa.Integer(), sa.ForeignKey("order_set_masters.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "item_type",
            sa.Enum("MEDICATION", "DIAGNOSTIC_LAB", "RADIOLOGY", "NURSING_CARE", name="ordersetitemtypeenum"),
            nullable=False,
        ),
        sa.Column("medication_id", sa.Integer(), sa.ForeignKey("medication_master.id"), nullable=True),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("service_catalog.id"), nullable=True),
        sa.Column("dosage_instruction", sa.String(length=255), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "priority",
            sa.Enum("ROUTINE", "URGENT", "STAT", name="ordersetpriorityenum"),
            nullable=False,
            server_default="ROUTINE",
        ),
        sa.Column("clinical_instructions", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_order_set_items_order_set_id", "order_set_items", ["order_set_id"])

    # 3. Create clinical_early_warning_scores table
    op.create_table(
        "clinical_early_warning_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("encounter_id", sa.Integer(), sa.ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vital_id", sa.Integer(), sa.ForeignKey("encounter_vitals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("evaluated_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("news2_score", sa.Integer(), nullable=False),
        sa.Column(
            "risk_level",
            sa.Enum("LOW", "MEDIUM", "HIGH", name="earlywarningriskenum"),
            nullable=False,
        ),
        sa.Column("qsofa_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_rrt_alert_triggered", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("clinical_recommendation", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_clinical_early_warning_scores_encounter_id", "clinical_early_warning_scores", ["encounter_id"])
    op.create_index(
        "ix_news2_encounter_created",
        "clinical_early_warning_scores",
        ["encounter_id", "created_at"],
    )

    # 4. Create lab_delta_check_configs table
    op.create_table(
        "lab_delta_check_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("service_catalog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parameter_name", sa.String(length=100), nullable=False),
        sa.Column("max_absolute_delta", sa.Float(), nullable=True),
        sa.Column("max_percentage_delta", sa.Float(), nullable=True),
        sa.Column("lookback_hours", sa.Integer(), nullable=False, server_default="48"),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="WARNING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("service_id", name="uq_lab_delta_check_service"),
    )

    # 5. Add delta check tracking columns to diagnostic_tasks
    op.add_column("diagnostic_tasks", sa.Column("delta_check_flag", sa.String(length=50), nullable=True))
    op.add_column("diagnostic_tasks", sa.Column("previous_result_value", sa.String(length=100), nullable=True))
    op.add_column("diagnostic_tasks", sa.Column("delta_percentage", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("diagnostic_tasks", "delta_percentage")
    op.drop_column("diagnostic_tasks", "previous_result_value")
    op.drop_column("diagnostic_tasks", "delta_check_flag")
    op.drop_table("lab_delta_check_configs")
    op.drop_table("clinical_early_warning_scores")
    op.drop_table("order_set_items")
    op.drop_table("order_set_masters")
