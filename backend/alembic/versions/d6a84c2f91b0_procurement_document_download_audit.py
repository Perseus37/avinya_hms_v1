"""record authorized procurement invoice document download requests"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6a84c2f91b0"
down_revision: Union[str, None] = "3e7a1c9d5b42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "procurement_document_download_audits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("invoice_number", sa.String(length=100), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["procurement_vendor_invoices.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_proc_doc_download_invoice_time",
        "procurement_document_download_audits",
        ["invoice_id", "requested_at"],
        unique=False,
    )
    op.create_index(
        "ix_proc_doc_download_actor_time",
        "procurement_document_download_audits",
        ["actor_user_id", "requested_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_proc_doc_download_actor_time",
        table_name="procurement_document_download_audits",
    )
    op.drop_index(
        "ix_proc_doc_download_invoice_time",
        table_name="procurement_document_download_audits",
    )
    op.drop_table("procurement_document_download_audits")
