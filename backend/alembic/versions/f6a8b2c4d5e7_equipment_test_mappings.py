"""add equipment test mappings table

Revision ID: f6a8b2c4d5e7
Revises: e5f7a9c1b384
Create Date: 2026-10-25 22:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a8b2c4d5e7"
down_revision: Union[str, None] = "e5f7a9c1b384"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "equipment_test_mappings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("equipment_id", sa.Integer(), sa.ForeignKey("equipment_master.id", ondelete="CASCADE"), nullable=False),
        sa.Column("analyzer_test_code", sa.String(length=50), nullable=False),
        sa.Column("analyzer_test_name", sa.String(length=100), nullable=True),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("service_catalog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("units", sa.String(length=30), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_equipment_test_mappings_equipment_id", "equipment_test_mappings", ["equipment_id"])
    op.create_index("ix_equipment_test_mappings_analyzer_test_code", "equipment_test_mappings", ["analyzer_test_code"])
    op.create_index("ix_equipment_test_mappings_service_id", "equipment_test_mappings", ["service_id"])


def downgrade() -> None:
    op.drop_index("ix_equipment_test_mappings_service_id", table_name="equipment_test_mappings")
    op.drop_index("ix_equipment_test_mappings_analyzer_test_code", table_name="equipment_test_mappings")
    op.drop_index("ix_equipment_test_mappings_equipment_id", table_name="equipment_test_mappings")
    op.drop_table("equipment_test_mappings")
