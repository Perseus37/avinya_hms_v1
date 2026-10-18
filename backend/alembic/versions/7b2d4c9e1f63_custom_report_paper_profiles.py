"""custom report paper profiles

Revision ID: 7b2d4c9e1f63
Revises: 43a1c9d7e206
Create Date: 2026-10-18 00:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7b2d4c9e1f63"
down_revision: Union[str, None] = "43a1c9d7e206"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in (
        "paper_width_mm",
        "paper_height_mm",
        "page_margin_top_mm",
        "page_margin_right_mm",
        "page_margin_bottom_mm",
        "page_margin_left_mm",
        "content_offset_x_mm",
        "content_offset_y_mm",
    ):
        op.add_column("report_templates", sa.Column(column, sa.Float(), nullable=True))


def downgrade() -> None:
    for column in (
        "content_offset_y_mm",
        "content_offset_x_mm",
        "page_margin_left_mm",
        "page_margin_bottom_mm",
        "page_margin_right_mm",
        "page_margin_top_mm",
        "paper_height_mm",
        "paper_width_mm",
    ):
        op.drop_column("report_templates", column)
