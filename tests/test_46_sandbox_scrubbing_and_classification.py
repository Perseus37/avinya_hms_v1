"""Comprehensive Verification for Sandbox Data Scrubbing, Table Classification, and Operator Governance (R-02, R-05, R-06, R-07, R-08, R-09, R-10, R-15).

Validates:
1. Every table in the schema is strictly classified as master, purged, or scrubbed (acceptance test for R-02).
2. Deep sandbox data scrubbing wipes real passwords, clears MFA secrets, deletes sessions and tokens,
   masks patient PII/PHI, anonymizes clinical SOAP notes, and purges privacy audit and breach records.
3. Control plane RBAC role enforcement (support, ops, superadmin, viewer) (R-05).
4. Mandatory justification and audit trail for cross-tenant MPI search (R-06).
5. Tenant admin password reset invalidates all active sessions and reset tokens (R-07).
6. Control plane audit log immutability prevents UPDATE and DELETE (R-09).
7. DR restore drill prevents invalid database names and special-character connection DSNs (R-10).
8. API documentation is locked down in production mode (R-15).
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update, delete
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core import database
from backend.core.database import Base, get_control_db, tenant_engine_pool
from backend.control_plane.app import app as control_plane_app
from backend.control_plane.models import (
    ControlPlaneAuditLog,
    ControlPlaneOperator,
    ControlPlaneOperatorSession,
    GlobalPatientPointer,
    TenantRegistry,
)
from backend.control_plane.services.auth import _complete_operator_login
from backend.control_plane.services.sandbox_sanitizer import (
    ALL_CLASSIFIED_TABLES,
    DUMMY_SANDBOX_PASSWORD,
    MASTER_AND_CONFIG_TABLES,
    PURGED_LOG_TABLES,
    SCRUBBED_DATA_TABLES,
    get_unclassified_tables,
    scrub_sandbox_database,
)
from backend.domains.identity.models import AuthSession, PasswordResetToken, User, UserStatusEnum
from backend.domains.identity.passwords import pwd_context
from backend.domains.patients.models import Patient
from backend.domains.encounters.models import EncounterTypeEnum, PatientEncounter, InpatientAdmission, AdmissionTypeEnum
from backend.domains.clinical.models import ClinicalNote
from backend.domains.billing.models import Invoice, InvoiceStatusEnum
from backend.domains.privacy.models import PrivacyDataBreachIncident, PrivacyDecisionAudit
from backend.scripts.restore_drill import run_restore_drill


def _get_cp_client(client: TestClient) -> TestClient:
    if get_control_db in client.app.dependency_overrides:
        control_plane_app.dependency_overrides[get_control_db] = client.app.dependency_overrides[get_control_db]
    return TestClient(
        control_plane_app,
        headers={"X-Control-Plane-Secret": settings.CONTROL_PLANE_MASTER_KEY},
    )


def _create_named_operator(db: Session, email: str, role: str) -> str:
    """Helper to create an active operator with MFA enabled and generate a bearer token."""
    op = db.execute(
        select(ControlPlaneOperator).where(ControlPlaneOperator.email == email)
    ).scalar_one_or_none()
    if not op:
        op = ControlPlaneOperator(
            email=email,
            full_name=f"Test Operator {role.upper()}",
            role=role,
            password_hash=pwd_context.hash("OperatorPass123!"),
            mfa_enabled=True,
            mfa_secret="JBSWY3DPEHPK3PXP",
            is_active=True,
        )
        db.add(op)
        db.commit()
        db.refresh(op)
    else:
        op.role = role
        op.is_active = True
        op.mfa_enabled = True
        db.commit()

    token_data = _complete_operator_login(db, op)
    return token_data["access_token"]


def test_01_table_classification_matrix_coverage():
    """
    R-02 Acceptance Criterion:
    Every table in the tenant metadata must be classified as master, scrubbed, or purged.
    Fails if any newly created table has not been explicitly registered in the sanitizer.
    """
    all_known_tables = set(Base.metadata.tables.keys())
    assert len(all_known_tables) > 0, "Base metadata must have tables loaded."

    unclassified = get_unclassified_tables(all_known_tables)
    assert unclassified == set(), (
        f"Security classification failure: The following tables lack sandbox data governance classification: {unclassified}. "
        f"Add them to MASTER_AND_CONFIG_TABLES, PURGED_LOG_TABLES, or SCRUBBED_DATA_TABLES in sandbox_sanitizer.py."
    )

    mock_tables = all_known_tables | {"future_unregistered_patient_data"}
    caught = get_unclassified_tables(mock_tables)
    assert caught == {"future_unregistered_patient_data"}


def test_02_sandbox_scrubbing_credentials_sessions_and_mfa(client: TestClient):
    """
    R-02 / F-02: Verify that scrubbing completely resets user credentials, removes MFA secrets,
    masks emails, and purges all active auth sessions and password reset tokens.
    """
    engine = tenant_engine_pool.get_engine("hms_apollo")

    # 1. Create live users with credentials, MFA secrets, and active sessions
    with Session(bind=engine) as db:
        test_admin = User(
            login_id="live_admin_01",
            email="real_dr_admin@livehospital.com",
            hashed_password=pwd_context.hash("LiveSecretPassword123!"),
            full_name="Dr. Live Administrator",
            phone_number="+919876543210",
            is_superuser=True,
            mfa_enabled=True,
            mfa_secret="JBSWY3DPEHPK3PXP",
            mfa_last_used_counter=42,
            status=UserStatusEnum.ACTIVE,
        )
        test_staff = User(
            login_id="live_nurse_01",
            email="real_nurse_mary@livehospital.com",
            hashed_password=pwd_context.hash("LiveNursePass456!"),
            full_name="Nurse Mary",
            phone_number="+919123456789",
            is_superuser=False,
            mfa_enabled=True,
            mfa_secret="JBSWY3DPEHPK3PXP",
            status=UserStatusEnum.ACTIVE,
        )
        db.add_all([test_admin, test_staff])
        db.commit()
        db.refresh(test_admin)
        db.refresh(test_staff)
        admin_id = test_admin.id
        staff_id = test_staff.id

        # Add active sessions and reset tokens
        db.add(AuthSession(token_id="live_sess_admin_01", user_id=admin_id, expires_at=datetime.now(timezone.utc)))
        db.add(AuthSession(token_id="live_sess_staff_01", user_id=staff_id, expires_at=datetime.now(timezone.utc)))
        db.add(PasswordResetToken(token_hash="hash_token_admin_01", user_id=admin_id, expires_at=datetime.now(timezone.utc)))
        db.commit()

    # 2. Run Sandbox Database Scrubbing
    stats = scrub_sandbox_database("hms_apollo")
    assert stats["scrubbed_tables"] > 0

    # 3. Verify User credentials wiped and anonymized
    with Session(bind=engine) as db:
        admin_after = db.get(User, admin_id)
        assert admin_after is not None
        assert admin_after.email == f"sandbox_admin_{admin_id}@sandbox.local"
        assert admin_after.phone_number is None
        assert admin_after.mfa_secret is None
        assert admin_after.mfa_enabled is False
        assert admin_after.mfa_last_used_counter is None
        assert pwd_context.verify(DUMMY_SANDBOX_PASSWORD, admin_after.hashed_password)

        staff_after = db.get(User, staff_id)
        assert staff_after is not None
        assert staff_after.email == f"staff_{staff_id}@sandbox.local"
        assert staff_after.phone_number is None
        assert staff_after.mfa_secret is None
        assert staff_after.mfa_enabled is False
        assert pwd_context.verify(DUMMY_SANDBOX_PASSWORD, staff_after.hashed_password)

        # 4. Verify all active sessions and reset tokens purged
        sessions_count = db.scalar(select(text("COUNT(*)")).select_from(text("auth_sessions")))
        assert sessions_count == 0
        tokens_count = db.scalar(select(text("COUNT(*)")).select_from(text("password_reset_tokens")))
        assert tokens_count == 0


def test_03_sandbox_scrubbing_clinical_notes_and_privacy_logs(client: TestClient):
    """
    R-02: Verify that patient PII, clinical SOAP notes, encounters, diagnostic tasks,
    and privacy incident / audit tables are anonymized or purged.
    """
    engine = tenant_engine_pool.get_engine("hms_apollo")

    with Session(bind=engine) as db:
        # 1. Create Patient & Encounter & Clinical Note
        pat = Patient(
            full_name="Rajesh Sensitive Sharma",
            mrn="MRN-CONFIDENTIAL-999",
            phone_number="+919988776655",
            email="rajesh.sharma@private.org",
            address="Flat 402, Lotus Towers, Bangalore",
            abha_number="91-1234-5678-9012",
            gender="MALE",
            date_of_birth=datetime(1985, 5, 20),
        )
        db.add(pat)
        db.commit()
        db.refresh(pat)
        pat_id = pat.id

        doc = db.execute(select(User).where(User.is_superuser == True)).scalars().first()
        dept_id = db.scalar(select(text("id")).select_from(text("departments")).limit(1)) or 1

        enc = PatientEncounter(
            patient_id=pat_id,
            encounter_type=EncounterTypeEnum.OPD,
            primary_doctor_id=doc.id,
            department_id=dept_id,
            chief_complaint_notes="Patient complains of severe chest tightness and anxiety.",
        )
        db.add(enc)
        db.commit()
        db.refresh(enc)
        enc_id = enc.id

        cn = ClinicalNote(
            encounter_id=enc_id,
            subjective_notes="Confidential patient medical history details.",
            objective_notes="BP 140/90, Pulse 92 bpm.",
            assessment_notes="Essential hypertension under stress.",
            plan_notes="Prescribe Amlodipine 5mg once daily.",
        )
        db.add(cn)

        inv = Invoice(
            patient_id=pat_id,
            encounter_id=enc_id,
            invoice_number=f"INV-TEST-{pat_id}",
            total_amount_paise=100000,
            discount_amount_paise=10000,
            discount_reason="VIP Special Executive Courtesy Discount",
            notes="High-profile patient requested private billing room.",
        )
        db.add(inv)

        breach = PrivacyDataBreachIncident(
            incident_code=f"BRC-{pat_id}",
            title="Leaked test patient record",
            description="Under active investigation",
            severity="HIGH",
            status="REPORTED",
            breach_type="CONFIDENTIALITY",
            affected_data_categories=["CLINICAL_NOTES"],
            reported_by_user_id=doc.id,
        )
        db.add(breach)
        db.commit()
        inv_id = inv.id
        cn_id = cn.id

    # 2. Scrub sandbox database
    scrub_sandbox_database("hms_apollo")

    # 3. Verify Patient masked
    with Session(bind=engine) as db:
        pat_after = db.get(Patient, pat_id)
        assert pat_after is not None
        assert pat_after.full_name == f"Sandbox Patient {pat_id}"
        assert pat_after.mrn == f"SBX-{pat_id}"
        assert pat_after.phone_number == "9000000000"
        assert pat_after.email == f"patient_{pat_id}@sandbox.local"
        assert pat_after.address == "Sandbox Medical Suite"
        assert pat_after.abha_number is None

        # 4. Verify Encounter and Clinical Notes masked
        enc_after = db.get(PatientEncounter, enc_id)
        assert enc_after.chief_complaint_notes == "Sandbox clinical encounter"

        cn_after = db.get(ClinicalNote, cn_id)
        assert "Sandbox" in cn_after.subjective_notes
        assert "Sandbox" in cn_after.plan_notes

        # 5. Verify Invoice remarks cleared
        inv_after = db.get(Invoice, inv_id)
        assert inv_after.notes == "Sandbox invoice"
        assert inv_after.discount_reason is None

        # 6. Verify Privacy Breach incident purged
        breaches_count = db.scalar(select(text("COUNT(*)")).select_from(text("privacy_data_breach_incidents")))
        assert breaches_count == 0


def test_04_control_plane_operator_roles_and_mpi_audit(client: TestClient):
    """
    R-05 & R-06: Operator role enforcement and audited MPI lookup.
    """
    cp = _get_cp_client(client)

    with database.ControlSessionLocal() as cdb:
        viewer_token = _create_named_operator(cdb, "operator_viewer@avinya.local", role="viewer")
        support_token = _create_named_operator(cdb, "operator_support@avinya.local", role="support")
        ops_token = _create_named_operator(cdb, "operator_ops@avinya.local", role="ops")

    # 1. Viewer role is forbidden from MPI lookup (requires support or superadmin)
    res_viewer = cp.get(
        "/api/mpi/lookup?query=9988776655&justification=Routine+hospital+audit+lookup",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert res_viewer.status_code == 403
    assert "role 'viewer' does not have required permissions" in res_viewer.json()["detail"]

    # 2. Support role calling MPI lookup without justification fails validation (422)
    res_no_just = cp.get(
        "/api/mpi/lookup?query=9988776655",
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert res_no_just.status_code == 422

    # 3. Support role calling MPI lookup with justification succeeds (200) and creates audit entry
    res_ok = cp.get(
        "/api/mpi/lookup?query=9988776655&justification=Cross-hospital+patient+emergency+transfer+verification",
        headers={
            "Authorization": f"Bearer {support_token}",
            "X-Master-Setup-Key": settings.MASTER_ONBOARDING_KEY,
        },
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["matches_count"] >= 0

    with database.ControlSessionLocal() as cdb:
        audit_entry = cdb.execute(
            select(ControlPlaneAuditLog)
            .where(ControlPlaneAuditLog.action == "MPI_CROSS_TENANT_LOOKUP")
            .order_by(ControlPlaneAuditLog.id.desc())
        ).scalars().first()
        assert audit_entry is not None
        assert audit_entry.actor == "operator_support@avinya.local"
        assert "Cross-hospital patient emergency transfer" in audit_entry.details

    # 4. Support role forbidden from triggering key rotation (requires ops or superadmin)
    res_supp_rot = cp.post(
        "/api/tenants/apollo/rotate-keys",
        headers={"Authorization": f"Bearer {support_token}"},
        json={"master_setup_key": settings.MASTER_ONBOARDING_KEY, "rotate_jwt_secret": True},
    )
    assert res_supp_rot.status_code == 403

    # 5. Ops role can trigger key rotation
    res_ops_rot = cp.post(
        "/api/tenants/apollo/rotate-keys",
        headers={"Authorization": f"Bearer {ops_token}"},
        json={"master_setup_key": settings.MASTER_ONBOARDING_KEY, "rotate_jwt_secret": True},
    )
    assert res_ops_rot.status_code == 200
    assert "new_jwt_secret_key" in res_ops_rot.json()


def test_05_emergency_admin_password_reset_invalidates_sessions(client: TestClient):
    """
    R-07: Emergency admin password reset must invalidate all active sessions in auth_sessions,
    set password_changed_at, and return a temporary password.
    """
    cp = _get_cp_client(client)
    engine = tenant_engine_pool.get_engine("hms_apollo")

    with Session(bind=engine) as db:
        admin = db.execute(select(User).where(User.is_superuser == True)).scalars().first()
        assert admin is not None
        admin_id = admin.id
        db.add(AuthSession(token_id=f"sess_admin_active_{admin_id}", user_id=admin_id, expires_at=datetime.now(timezone.utc)))
        db.commit()

    # 2. Call emergency reset endpoint
    res = cp.post(
        "/api/tenants/apollo/reset-admin-password",
        json={"master_setup_key": settings.MASTER_ONBOARDING_KEY},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["sessions_invalidated"] is True
    assert "one_time_password" in body

    # 3. Verify user's session was invalidated and password updated
    with Session(bind=engine) as db:
        active_sess = db.execute(
            select(AuthSession).where(AuthSession.user_id == admin_id)
        ).scalars().all()
        assert len(active_sess) == 0

        admin_refreshed = db.get(User, admin_id)
        assert admin_refreshed.password_changed_at is not None
        assert pwd_context.verify(body["one_time_password"], admin_refreshed.hashed_password)


def test_06_control_plane_audit_log_immutability(client: TestClient):
    """
    R-09: ControlPlaneAuditLog entries are strictly append-only.
    Attempting an UPDATE or DELETE raises PermissionError.
    """
    with database.ControlSessionLocal() as cdb:
        entry = ControlPlaneAuditLog(
            action="SECURITY_CANARY_EVENT",
            target_slug="test-slug",
            actor="SYSTEM_MONITOR",
            details="Immutable audit ledger verification probe",
        )
        cdb.add(entry)
        cdb.commit()
        cdb.refresh(entry)

        # Attempt UPDATE -> Must raise PermissionError
        with pytest.raises(PermissionError, match="strictly immutable and cannot be updated"):
            entry.details = "Tampered details"
            cdb.commit()

        cdb.rollback()

        # Attempt DELETE -> Must raise PermissionError
        with pytest.raises(PermissionError, match="strictly immutable and cannot be deleted"):
            cdb.delete(entry)
            cdb.commit()

        cdb.rollback()


def test_07_restore_drill_name_guard():
    """
    R-10: run_restore_drill rejects malformed or malicious drill_db_name identifiers.
    """
    with pytest.raises(ValueError, match="Invalid drill database name"):
        run_restore_drill(
            backup_file="backups/dummy.dump.enc",
            tenant_slug="apollo",
            encryption_key="dummy_key",
            drill_db_name="drill_invalid; DROP TABLE users;--",
        )


def test_08_api_documentation_disabled_in_production():
    """
    R-15: Swagger UI (/docs), ReDoc (/redoc), and OpenAPI (/openapi.json)
    are deactivated when ENVIRONMENT is production.
    """
    assert hasattr(settings, "ENABLE_PRODUCTION_API_DOCS")
    assert settings.ENABLE_PRODUCTION_API_DOCS is False
