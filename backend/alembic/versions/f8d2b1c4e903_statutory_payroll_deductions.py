"""add statutory indian payroll deduction fields (M-05)

Revision ID: f8d2b1c4e903
Revises: e5a1c8f3b207
Create Date: 2026-10-25 08:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8d2b1c4e903"
down_revision: Union[str, None] = "e5a1c8f3b207"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("payroll_lines") as batch_op:
        batch_op.add_column(sa.Column("pf_employee_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("pf_employer_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("esi_employee_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("esi_employer_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("professional_tax_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("tds_paise", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("statutory_details_json", sa.Text(), nullable=True))

        batch_op.create_check_constraint("ck_payroll_pf_emp_non_neg", "pf_employee_paise >= 0")
        batch_op.create_check_constraint("ck_payroll_pf_empr_non_neg", "pf_employer_paise >= 0")
        batch_op.create_check_constraint("ck_payroll_esi_emp_non_neg", "esi_employee_paise >= 0")
        batch_op.create_check_constraint("ck_payroll_esi_empr_non_neg", "esi_employer_paise >= 0")
        batch_op.create_check_constraint("ck_payroll_pt_non_neg", "professional_tax_paise >= 0")
        batch_op.create_check_constraint("ck_payroll_tds_non_neg", "tds_paise >= 0")


def downgrade() -> None:
    with op.batch_alter_table("payroll_lines") as batch_op:
        batch_op.drop_constraint("ck_payroll_tds_non_neg", type_="check")
        batch_op.drop_constraint("ck_payroll_pt_non_neg", type_="check")
        batch_op.drop_constraint("ck_payroll_esi_empr_non_neg", type_="check")
        batch_op.drop_constraint("ck_payroll_esi_emp_non_neg", type_="check")
        batch_op.drop_constraint("ck_payroll_pf_empr_non_neg", type_="check")
        batch_op.drop_constraint("ck_payroll_pf_emp_non_neg", type_="check")

        batch_op.drop_column("statutory_details_json")
        batch_op.drop_column("tds_paise")
        batch_op.drop_column("professional_tax_paise")
        batch_op.drop_column("esi_employer_paise")
        batch_op.drop_column("esi_employee_paise")
        batch_op.drop_column("pf_employer_paise")
        batch_op.drop_column("pf_employee_paise")

