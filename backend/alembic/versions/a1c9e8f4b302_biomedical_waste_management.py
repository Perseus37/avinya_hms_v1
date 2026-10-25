"""add biomedical waste management tables under BMW rules 2016 (M-09)

Revision ID: a1c9e8f4b302
Revises: f8d2b1c4e903
Create Date: 2026-10-25 09:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c9e8f4b302"
down_revision: Union[str, None] = "f8d2b1c4e903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create biomedical_waste_manifests table
    op.create_table(
        "biomedical_waste_manifests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("manifest_number", sa.String(length=50), nullable=False),
        sa.Column("cbwtf_operator_name", sa.String(length=150), nullable=False),
        sa.Column("cbwtf_vehicle_number", sa.String(length=50), nullable=False),
        sa.Column("cbwtf_driver_name", sa.String(length=100), nullable=False),
        sa.Column("total_bags_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_weight_grams", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("yellow_weight_grams", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("red_weight_grams", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("white_weight_grams", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blue_weight_grams", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("supervisor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("pickup_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("total_bags_count >= 0", name="ck_bmw_manifest_bags_non_neg"),
        sa.CheckConstraint("total_weight_grams >= 0", name="ck_bmw_manifest_weight_non_neg"),
    )
    op.create_index("ix_biomedical_waste_manifests_manifest_number", "biomedical_waste_manifests", ["manifest_number"], unique=True)

    # 2. Create biomedical_waste_bags table
    op.create_table(
        "biomedical_waste_bags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("color_category", sa.String(length=20), nullable=False),
        sa.Column("weight_grams", sa.Integer(), nullable=False),
        sa.Column("source_department", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="LOGGED"),
        sa.Column("manifest_id", sa.Integer(), sa.ForeignKey("biomedical_waste_manifests.id"), nullable=True),
        sa.Column("waste_description", sa.Text(), nullable=True),
        sa.Column("logged_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handed_over_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("weight_grams > 0", name="ck_bmw_bag_weight_pos"),
        sa.CheckConstraint("color_category IN ('YELLOW', 'RED', 'WHITE', 'BLUE')", name="ck_bmw_bag_color_category"),
        sa.CheckConstraint("status IN ('LOGGED', 'STORED', 'HANDED_OVER', 'DISPOSED')", name="ck_bmw_bag_status"),
    )
    op.create_index("ix_biomedical_waste_bags_barcode", "biomedical_waste_bags", ["barcode"], unique=True)
    op.create_index("ix_biomedical_waste_bags_color_category", "biomedical_waste_bags", ["color_category"])
    op.create_index("ix_biomedical_waste_bags_source_department", "biomedical_waste_bags", ["source_department"])
    op.create_index("ix_biomedical_waste_bags_status", "biomedical_waste_bags", ["status"])
    op.create_index("ix_biomedical_waste_bags_manifest_id", "biomedical_waste_bags", ["manifest_id"])
    op.create_index("ix_biomedical_waste_bags_logged_at", "biomedical_waste_bags", ["logged_at"])


def downgrade() -> None:
    op.drop_table("biomedical_waste_bags")
    op.drop_table("biomedical_waste_manifests")

