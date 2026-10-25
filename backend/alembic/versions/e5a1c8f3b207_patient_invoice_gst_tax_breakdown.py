"""add patient invoice gst tax breakdown and lines

Revision ID: e5a1c8f3b207
Revises: c7f3e1b9a240
Create Date: 2026-10-25 07:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a1c8f3b207"
down_revision: Union[str, None] = "c7f3e1b9a240"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update invoice_line_items
    with op.batch_alter_table("invoice_line_items") as batch_op:
        batch_op.add_column(sa.Column("gst_rate_percent", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("tax_amount_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("hsn_sac_code", sa.String(length=50), nullable=True))
        batch_op.create_check_constraint("ck_line_item_gst_rate", "gst_rate_percent >= 0 AND gst_rate_percent <= 100")
        batch_op.create_check_constraint("ck_line_item_tax_non_neg", "tax_amount_paise >= 0")

    # 2. Update invoices
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(sa.Column("tax_amount_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("cgst_amount_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("sgst_amount_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("igst_amount_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("is_tax_exempt", sa.Boolean(), nullable=False, server_default=sa.text("true")))
        batch_op.add_column(sa.Column("hsn_sac_code", sa.String(length=50), nullable=True))
        batch_op.create_check_constraint("ck_invoice_tax_non_neg", "tax_amount_paise >= 0")
        batch_op.create_check_constraint("ck_invoice_cgst_non_neg", "cgst_amount_paise >= 0")
        batch_op.create_check_constraint("ck_invoice_sgst_non_neg", "sgst_amount_paise >= 0")
        batch_op.create_check_constraint("ck_invoice_igst_non_neg", "igst_amount_paise >= 0")
        batch_op.drop_constraint("ck_invoice_no_overpayment", type_="check")
        batch_op.create_check_constraint(
            "ck_invoice_no_overpayment",
            "amount_paid_paise <= (total_amount_paise + tax_amount_paise - discount_amount_paise)",
        )


def downgrade() -> None:
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_constraint("ck_invoice_no_overpayment", type_="check")
        batch_op.create_check_constraint(
            "ck_invoice_no_overpayment",
            "amount_paid_paise <= (total_amount_paise - discount_amount_paise)",
        )
        batch_op.drop_constraint("ck_invoice_igst_non_neg", type_="check")
        batch_op.drop_constraint("ck_invoice_sgst_non_neg", type_="check")
        batch_op.drop_constraint("ck_invoice_cgst_non_neg", type_="check")
        batch_op.drop_constraint("ck_invoice_tax_non_neg", type_="check")
        batch_op.drop_column("hsn_sac_code")
        batch_op.drop_column("is_tax_exempt")
        batch_op.drop_column("igst_amount_paise")
        batch_op.drop_column("sgst_amount_paise")
        batch_op.drop_column("cgst_amount_paise")
        batch_op.drop_column("tax_amount_paise")

    with op.batch_alter_table("invoice_line_items") as batch_op:
        batch_op.drop_constraint("ck_line_item_tax_non_neg", type_="check")
        batch_op.drop_constraint("ck_line_item_gst_rate", type_="check")
        batch_op.drop_column("hsn_sac_code")
        batch_op.drop_column("tax_amount_paise")
        batch_op.drop_column("gst_rate_percent")

