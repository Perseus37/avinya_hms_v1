"""add is_interstate to invoices and route_matched to bcma_scan_audit_logs

Revision ID: d8a2f1c4e705
Revises: c1f4e8b2a937
Create Date: 2026-10-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d8a2f1c4e705"
down_revision = "c1f4e8b2a937"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(
            sa.Column("is_interstate", sa.Boolean(), server_default=sa.false(), nullable=False)
        )

    with op.batch_alter_table("bcma_scan_audit_logs") as batch_op:
        batch_op.add_column(
            sa.Column("route_matched", sa.Boolean(), server_default=sa.true(), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("bcma_scan_audit_logs") as batch_op:
        batch_op.drop_column("route_matched")

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_column("is_interstate")

