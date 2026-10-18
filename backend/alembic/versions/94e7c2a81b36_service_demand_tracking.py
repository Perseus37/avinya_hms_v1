"""service demand tracking

Revision ID: 94e7c2a81b36
Revises: 82ca74d1b605
Create Date: 2026-10-18 01:35:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "94e7c2a81b36"
down_revision: Union[str, None] = "82ca74d1b605"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_demand_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("requested_service_name", sa.String(length=200), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "SERVICE_UNAVAILABLE",
                "NO_APPOINTMENT_CAPACITY",
                "NO_SPECIALIST",
                "EQUIPMENT_UNAVAILABLE",
                "OTHER",
                name="servicedemandreasonenum",
            ),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "OPD",
                "DIAGNOSTICS",
                "REFERRAL",
                "OTHER",
                name="servicedemandsourceenum",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "REFERRED_OUT",
                "SERVICE_AVAILABLE",
                "RESOLVED",
                "CLOSED",
                name="servicedemandstatusenum",
            ),
            server_default="OPEN",
            nullable=False,
        ),
        sa.Column("patient_id", sa.Integer(), nullable=True),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("service_id", sa.Integer(), nullable=True),
        sa.Column("requested_by_id", sa.Integer(), nullable=False),
        sa.Column("external_facility_name", sa.String(length=200), nullable=True),
        sa.Column("resolution_notes", sa.String(length=1000), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["service_catalog.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_service_demand_created_status",
        "service_demand_requests",
        ["created_at", "status"],
        unique=False,
    )
    op.create_index(
        "ix_service_demand_department_created",
        "service_demand_requests",
        ["department_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "service_demand_status_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("changed_by_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(
            ["changed_by_id"], ["users.id"]
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["service_demand_requests.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_service_demand_history_request_time",
        "service_demand_status_history",
        ["request_id", "changed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_service_demand_history_request_time",
        table_name="service_demand_status_history",
    )
    op.drop_table("service_demand_status_history")
    op.drop_index(
        "ix_service_demand_department_created",
        table_name="service_demand_requests",
    )
    op.drop_index(
        "ix_service_demand_created_status",
        table_name="service_demand_requests",
    )
    op.drop_table("service_demand_requests")
