"""add staff attendance, leave, and auditable payroll MVP

Revision ID: 7c4e2a9b6d10
Revises: 6a2f9c1d7e43
Create Date: 2026-10-19 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c4e2a9b6d10"
down_revision: Union[str, None] = "6a2f9c1d7e43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def _audit_columns():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "attendance_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("check_in", sa.Time(), nullable=True),
        sa.Column("check_out", sa.Time(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "work_date", name="uq_attendance_user_date"),
    )
    op.create_index("ix_attendance_records_user_id", "attendance_records", ["user_id"])
    op.create_index("ix_attendance_records_work_date", "attendance_records", ["work_date"])

    op.create_table(
        "leave_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("leave_type", sa.String(length=40), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_leave_requests_user_id", "leave_requests", ["user_id"])
    op.create_index("ix_leave_requests_start_date", "leave_requests", ["start_date"])

    op.create_table(
        "salary_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("monthly_fixed_paise", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "effective_from", name="uq_salary_profile_effective"),
    )
    op.create_index("ix_salary_profiles_user_id", "salary_profiles", ["user_id"])
    op.create_index("ix_salary_profiles_effective_from", "salary_profiles", ["effective_from"])

    op.create_table(
        "payroll_periods",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("period_key", sa.String(length=7), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("working_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finalized_by_user_id", sa.Integer(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["finalized_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period_key", name="uq_payroll_period_key"),
    )

    op.create_table(
        "payroll_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("salary_profile_id", sa.Integer(), nullable=False),
        sa.Column("monthly_fixed_paise", sa.Integer(), nullable=False),
        sa.Column("paid_days", sa.Numeric(precision=7, scale=2), nullable=False),
        sa.Column("working_days", sa.Integer(), nullable=False),
        sa.Column("prorated_fixed_paise", sa.Integer(), nullable=False),
        sa.Column("additional_earnings_paise", sa.Integer(), nullable=False),
        sa.Column("deductions_paise", sa.Integer(), nullable=False),
        sa.Column("gross_paise", sa.Integer(), nullable=False),
        sa.Column("net_paise", sa.Integer(), nullable=False),
        sa.Column("calculation_note", sa.Text(), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["period_id"], ["payroll_periods.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["salary_profile_id"], ["salary_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period_id", "user_id", name="uq_payroll_line_period_user"),
    )
    op.create_index("ix_payroll_lines_period_id", "payroll_lines", ["period_id"])
    op.create_index("ix_payroll_lines_user_id", "payroll_lines", ["user_id"])

    op.create_table(
        "payroll_calculation_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("input_snapshot", sa.Text(), nullable=False),
        sa.Column("output_snapshot", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["line_id"], ["payroll_lines.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payroll_calculation_events_line_id",
        "payroll_calculation_events",
        ["line_id"],
    )

    connection = op.get_bind()
    permissions = (
        ("hr:read", "View staff attendance, leave, salary profiles and payroll", "Workforce"),
        ("hr:write", "Record attendance, leave requests and salary profiles", "Workforce"),
        ("hr:approve", "Approve leave requests and finalize payroll periods", "Workforce"),
        ("hr:payroll", "Create payroll periods and calculate payroll lines", "Workforce"),
        ("payroll:read", "View confidential salary and payroll records", "Payroll"),
    )
    for code, description, module in permissions:
        if connection.execute(
            sa.text("SELECT 1 FROM permissions WHERE code = :code"), {"code": code}
        ).first() is None:
            connection.execute(
                sa.text(
                    "INSERT INTO permissions "
                    "(code, description, module, created_at, updated_at) "
                    "VALUES (:code, :description, :module, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"code": code, "description": description, "module": module},
            )
    for role_code, actions in (
        ("ROLE_ADMIN", ("read", "write", "approve", "payroll", "payroll:read")),
        ("ROLE_AUDITOR_READONLY", ("read",)),
    ):
        role_id = connection.execute(
            sa.text("SELECT id FROM roles WHERE code = :code"), {"code": role_code}
        ).scalar_one_or_none()
        if role_id is None:
            continue
        for action in actions:
            permission_id = connection.execute(
                sa.text("SELECT id FROM permissions WHERE code = :code"),
                {"code": action if action == "payroll:read" else f"hr:{action}"},
            ).scalar_one()
            if connection.execute(
                sa.text(
                    "SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id"
                ),
                {"role_id": role_id, "permission_id": permission_id},
            ).first() is None:
                connection.execute(
                    sa.text(
                        "INSERT INTO role_permissions (role_id, permission_id) "
                        "VALUES (:role_id, :permission_id)"
                    ),
                    {"role_id": role_id, "permission_id": permission_id},
                )


def downgrade() -> None:
    connection = op.get_bind()
    for code in ("hr:read", "hr:write", "hr:approve", "hr:payroll", "payroll:read"):
        permission_id = connection.execute(
            sa.text("SELECT id FROM permissions WHERE code = :code"), {"code": code}
        ).scalar_one_or_none()
        if permission_id is not None:
            connection.execute(
                sa.text("DELETE FROM role_permissions WHERE permission_id = :id"),
                {"id": permission_id},
            )
            connection.execute(
                sa.text("DELETE FROM permissions WHERE id = :id"), {"id": permission_id}
            )
    op.drop_index("ix_payroll_calculation_events_line_id", table_name="payroll_calculation_events")
    op.drop_index("ix_payroll_lines_user_id", table_name="payroll_lines")
    op.drop_index("ix_payroll_lines_period_id", table_name="payroll_lines")
    op.drop_index("ix_salary_profiles_effective_from", table_name="salary_profiles")
    op.drop_index("ix_salary_profiles_user_id", table_name="salary_profiles")
    op.drop_index("ix_leave_requests_start_date", table_name="leave_requests")
    op.drop_index("ix_leave_requests_user_id", table_name="leave_requests")
    op.drop_index("ix_attendance_records_work_date", table_name="attendance_records")
    op.drop_index("ix_attendance_records_user_id", table_name="attendance_records")
    op.drop_table("payroll_calculation_events")
    op.drop_table("payroll_lines")
    op.drop_table("payroll_periods")
    op.drop_table("salary_profiles")
    op.drop_table("leave_requests")
    op.drop_table("attendance_records")
