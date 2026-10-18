"""add ambulance, dietary, CSSD, and mortuary MVP workflows

Revision ID: 5d8a1e7c3f42
Revises: 4c1d7b9e2a63
Create Date: 2026-10-19 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5d8a1e7c3f42"
down_revision: Union[str, None] = "4c1d7b9e2a63"
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
        "ambulance_vehicles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("registration_number", sa.String(length=20), nullable=False),
        sa.Column("call_sign", sa.String(length=30), nullable=False),
        sa.Column("vehicle_type", sa.String(length=30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("registration_number", name="uq_ambulance_registration"),
        sa.UniqueConstraint("call_sign", name="uq_ambulance_call_sign"),
    )
    op.create_table(
        "ambulance_dispatches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=True),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("pickup_location", sa.String(length=255), nullable=False),
        sa.Column("destination", sa.String(length=255), nullable=False),
        sa.Column("acuity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["vehicle_id"], ["ambulance_vehicles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ambulance_dispatches_vehicle_id", "ambulance_dispatches", ["vehicle_id"])
    op.create_table(
        "dietary_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("diet_type", sa.String(length=50), nullable=False),
        sa.Column("meal", sa.String(length=20), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("allergy_notes", sa.Text(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dietary_orders_patient_id", "dietary_orders", ["patient_id"])
    op.create_table(
        "cssd_instrument_sets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_cssd_instrument_set_code"),
    )
    op.create_table(
        "cssd_sterilization_batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_number", sa.String(length=40), nullable=False),
        sa.Column("instrument_set_id", sa.Integer(), nullable=False),
        sa.Column("sterilizer_id", sa.String(length=40), nullable=False),
        sa.Column("cycle_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("cycle_started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cycle_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chemical_indicator_passed", sa.Boolean(), nullable=True),
        sa.Column("biological_indicator_passed", sa.Boolean(), nullable=True),
        sa.Column("release_notes", sa.Text(), nullable=True),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["instrument_set_id"], ["cssd_instrument_sets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_number"),
    )
    op.create_index(
        "ix_cssd_sterilization_batches_instrument_set_id",
        "cssd_sterilization_batches",
        ["instrument_set_id"],
    )
    op.create_table(
        "mortuary_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=True),
        sa.Column("case_reference", sa.String(length=40), nullable=False),
        sa.Column("deceased_name", sa.String(length=100), nullable=False),
        sa.Column("source_department", sa.String(length=100), nullable=True),
        sa.Column("custody_location", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_recipient", sa.String(length=100), nullable=True),
        sa.Column("release_identity_verified", sa.Boolean(), nullable=True),
        sa.Column("release_witness", sa.String(length=100), nullable=True),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_reference"),
    )
    op.create_index("ix_mortuary_cases_patient_id", "mortuary_cases", ["patient_id"])
    op.create_table(
        "service_workflow_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("domain", sa.String(length=20), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_workflow_events_domain", "service_workflow_events", ["domain"])
    op.create_index("ix_service_workflow_events_record_id", "service_workflow_events", ["record_id"])

    connection = op.get_bind()
    permission_rows = (
        ("ambulance:read", "View ambulance vehicles and dispatches", "Ambulance"),
        ("ambulance:write", "Register vehicles and manage emergency dispatches", "Ambulance"),
        ("dietary:read", "View patient meal orders and delivery status", "Dietary"),
        ("dietary:write", "Create and fulfill patient meal orders", "Dietary"),
        ("cssd:read", "View sterile instrument sets and cycle batches", "CSSD"),
        ("cssd:write", "Register instrument sets and document sterilization cycles", "CSSD"),
        ("mortuary:read", "View mortuary custody records", "Mortuary"),
        ("mortuary:write", "Receive and release mortuary cases", "Mortuary"),
    )
    for code, description, module in permission_rows:
        row = connection.execute(
            sa.text("SELECT id FROM permissions WHERE code = :code"), {"code": code}
        ).first()
        if row is None:
            connection.execute(
                sa.text(
                    "INSERT INTO permissions "
                    "(code, description, module, created_at, updated_at) "
                    "VALUES (:code, :description, :module, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"code": code, "description": description, "module": module},
            )
    for role_code, suffix in (
        ("ROLE_ADMIN", ("read", "write")),
        ("ROLE_AUDITOR_READONLY", ("read",)),
    ):
        role_id = connection.execute(
            sa.text("SELECT id FROM roles WHERE code = :code"),
            {"code": role_code},
        ).scalar_one_or_none()
        if role_id is None:
            continue
        for module, actions in (
            ("ambulance", suffix),
            ("dietary", suffix),
            ("cssd", suffix),
            ("mortuary", suffix),
        ):
            for action in actions:
                permission_id = connection.execute(
                    sa.text("SELECT id FROM permissions WHERE code = :code"),
                    {"code": f"{module}:{action}"},
                ).scalar_one()
                exists = connection.execute(
                    sa.text(
                        "SELECT 1 FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_id = :permission_id"
                    ),
                    {"role_id": role_id, "permission_id": permission_id},
                ).first()
                if exists is None:
                    connection.execute(
                        sa.text(
                            "INSERT INTO role_permissions (role_id, permission_id) "
                            "VALUES (:role_id, :permission_id)"
                        ),
                        {"role_id": role_id, "permission_id": permission_id},
                    )


def downgrade() -> None:
    connection = op.get_bind()
    for code, _, _ in (
        ("ambulance:read", "", ""),
        ("ambulance:write", "", ""),
        ("dietary:read", "", ""),
        ("dietary:write", "", ""),
        ("cssd:read", "", ""),
        ("cssd:write", "", ""),
        ("mortuary:read", "", ""),
        ("mortuary:write", "", ""),
    ):
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
    for index, table in (
        ("ix_service_workflow_events_record_id", "service_workflow_events"),
        ("ix_service_workflow_events_domain", "service_workflow_events"),
        ("ix_mortuary_cases_patient_id", "mortuary_cases"),
        ("ix_cssd_sterilization_batches_instrument_set_id", "cssd_sterilization_batches"),
        ("ix_dietary_orders_patient_id", "dietary_orders"),
        ("ix_ambulance_dispatches_vehicle_id", "ambulance_dispatches"),
    ):
        op.drop_index(index, table_name=table)
    for table in (
        "service_workflow_events",
        "mortuary_cases",
        "cssd_sterilization_batches",
        "cssd_instrument_sets",
        "dietary_orders",
        "ambulance_dispatches",
        "ambulance_vehicles",
    ):
        op.drop_table(table)
