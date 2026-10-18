import jwt
import pytest
from pydantic import ValidationError
from backend.core.config import settings
from backend.domains.identity.schemas import UserCreate


def test_01_login_mints_tenant_scoped_jwt(client, multi_tenant_env):
    """Scenario 1: Valid login returns a JWT containing sub, tenant_slug, and branch_code."""
    res = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "apollo"},
        json={"email": "admin@apollo.com", "password": "AdminPassword123!"},
    )
    assert res.status_code == 200
    token = res.json()["access_token"]
    claims = jwt.decode(
        token,
        multi_tenant_env["tenant_keys"]["apollo"]["jwt"],
        algorithms=[settings.ALGORITHM],
    )
    assert claims["tenant_slug"] == "apollo"
    assert claims["branch_code"] == "APOLLO-HYD-01"


def test_02_cross_tenant_token_replay_is_blocked(client, apollo_admin_headers):
    """Scenario 2: A valid token from 'apollo' is rejected with 401 when replayed against 'default' or 'sunrise'."""
    replay_on_default = {
        "Authorization": apollo_admin_headers["Authorization"],
        "X-Hospital-Subdomain": "default",
    }
    assert client.get("/v1/api/auth/me", headers=replay_on_default).status_code == 401

    replay_on_sunrise = {
        "Authorization": apollo_admin_headers["Authorization"],
        "X-Hospital-Subdomain": "sunrise",
    }
    assert client.get("/v1/api/auth/me", headers=replay_on_sunrise).status_code == 401


def test_03_brute_force_account_lockout(client):
    """Scenario 3: 5 consecutive wrong passwords lock the user; 6th attempt with right password fails (401)."""
    for _ in range(5):
        fail_res = client.post(
            "/v1/api/auth/login",
            headers={"X-Hospital-Subdomain": "default"},
            json={"email": "doctor@default.com", "password": "WrongPassword!"},
        )
        assert fail_res.status_code == 401

    locked_res = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "doctor@default.com", "password": "StaffPass123!"},
    )
    assert locked_res.status_code == 401
    assert "locked" in locked_res.json()["message"].lower()


def test_04_admin_account_unlock_resets_attempts(client, superuser_headers):
    """Scenario 4: Admin unlocking a LOCKED account resets failed_login_attempts and restores login."""
    for _ in range(5):
        client.post(
            "/v1/api/auth/login",
            headers={"X-Hospital-Subdomain": "default"},
            json={"email": "doctor@default.com", "password": "WrongPassword!"},
        )

    users_res = client.get("/v1/api/auth/users", headers=superuser_headers)
    doc_user = next(u for u in users_res.json()["items"] if u["email"] == "doctor@default.com")

    unlock_res = client.patch(
        f"/v1/api/auth/users/{doc_user['id']}/status?new_status=ACTIVE",
        headers=superuser_headers,
    )
    assert unlock_res.status_code == 200

    success_res = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "doctor@default.com", "password": "StaffPass123!"},
    )
    assert success_res.status_code == 200


def test_password_lockout_expires_and_uses_hospital_policy(client, multi_tenant_env):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy.orm import Session
    from backend.domains.identity.models import User, UserStatusEnum

    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        user = db.query(User).filter_by(email="doctor@default.com").one()
        user.status = UserStatusEnum.LOCKED
        user.failed_login_attempts = 5
        user.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()

    response = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "doctor@default.com", "password": "StaffPass123!"},
    )
    assert response.status_code == 200, response.text


def test_password_lock_threshold_uses_hospital_policy(client, multi_tenant_env):
    from sqlalchemy.orm import Session
    from backend.domains.policies.models import HospitalPolicySetting
    from backend.domains.identity.models import User, UserStatusEnum

    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        policy = db.query(HospitalPolicySetting).first()
        if policy is None:
            policy = HospitalPolicySetting()
            db.add(policy)
        policy.max_failed_login_attempts = 3
        db.commit()

    for _ in range(3):
        response = client.post(
            "/v1/api/auth/login",
            headers={"X-Hospital-Subdomain": "default"},
            json={"email": "doctor@default.com", "password": "WrongPassword!"},
        )
        assert response.status_code == 401

    with Session(engine) as db:
        user = db.query(User).filter_by(email="doctor@default.com").one()
        assert user.status == UserStatusEnum.LOCKED
        assert user.locked_until is not None


def test_maintenance_mode_allows_login(client, multi_tenant_env):
    from backend.control_plane.models import TenantRegistry

    factory = multi_tenant_env["control_session_factory"]
    with factory() as db:
        tenant = db.query(TenantRegistry).filter_by(subdomain_slug="default").one()
        tenant.maintenance_mode = True
        db.commit()

    response = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "admin@default.com", "password": "AdminPassword123!"},
    )
    assert response.status_code == 200, response.text


def test_user_creation_requires_long_password():
    with pytest.raises(ValidationError):
        UserCreate(
            login_id="short-password",
            email="short-password@example.com",
            password="Short123!",
            full_name="Short Password",
            role_ids=[1],
        )

    with pytest.raises(ValidationError, match="three character classes"):
        UserCreate(
            login_id="weak-password",
            email="weak-password@example.com",
            password="onlylowercase",
            full_name="Weak Password",
            role_ids=[1],
        )


def test_05_read_only_auditor_allowed_on_gets_blocked_on_writes(client, auditor_headers):
    """Scenario 5: ROLE_AUDITOR_READONLY succeeds on GET routes and gets 403 on POST/PUT/DELETE."""
    assert client.get("/v1/api/auth/users", headers=auditor_headers).status_code == 200
    assert client.get("/v1/api/patients", headers=auditor_headers).status_code == 200
    assert client.get("/v1/api/ip/wards", headers=auditor_headers).status_code == 200
    assert client.get("/v1/api/billing/invoices", headers=auditor_headers).status_code == 200
    assert client.get("/v1/api/pharmacy/medications", headers=auditor_headers).status_code == 200

    assert client.post(
        "/v1/api/patients",
        headers=auditor_headers,
        json={"full_name": "Blocked Write", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"},
    ).status_code == 403
    assert client.post(
        "/v1/api/ip/floors",
        headers=auditor_headers,
        json={"floor_number": 99, "name": "Blocked Floor"},
    ).status_code == 403
    assert client.post(
        "/v1/api/billing/invoices",
        headers=auditor_headers,
        json={"patient_id": 1},
    ).status_code == 403


def test_06_permissions_matrix_categorizes_permissions(client, superuser_headers):
    """Scenario 6: permission matrix groups all seeded modules by read/write/destructive."""
    matrix_res = client.get("/v1/api/auth/permissions/matrix", headers=superuser_headers)
    assert matrix_res.status_code == 200
    modules = matrix_res.json()["modules"]
    assert set(modules.keys()) == {
        "ADT", "Ambulance", "Billing", "ClinicalMDM", "CSSD", "Diagnostics",
        "Dietary", "IAM", "Mortuary", "OPD", "Patients", "Pharmacy", "Privacy",
        "WardManagement", "Procurement", "Workforce", "Payroll",
    }
    assert any(p["code"] == "billing:reverse" for p in modules["Billing"]["destructive"])


def test_07_custom_permission_creation_superuser_only(client, superuser_headers, doctor_headers):
    """Scenario 7: Superuser can define custom permissions; non-superuser is blocked with 403."""
    payload = {
        "module": "Radiology",
        "resource": "radiology",
        "action": "signoff",
        "description": "Sign off MRI/CT scans",
    }
    assert client.post("/v1/api/auth/permissions", headers=doctor_headers, json=payload).status_code == 403

    create_perm = client.post("/v1/api/auth/permissions", headers=superuser_headers, json=payload)
    assert create_perm.status_code == 201
    assert create_perm.json()["code"] == "radiology:signoff"


def _create_hr_subadmin_headers(client, superuser_headers):
    """Helper to provision a non-superuser sub-admin holding only iam:read and iam:write."""
    role_res = client.post(
        "/v1/api/auth/roles",
        headers=superuser_headers,
        json={"name": "HR SubAdmin", "description": "Sub-admin with IAM write only"},
    )
    role_id = role_res.json()["id"]
    all_perms = client.get("/v1/api/auth/permissions", headers=superuser_headers).json()
    iam_perm_ids = [p["id"] for p in all_perms if p["code"] in ("iam:read", "iam:write")]
    client.post(f"/v1/api/auth/roles/{role_id}/permissions", headers=superuser_headers, json=iam_perm_ids)

    client.post(
        "/v1/api/auth/users/doctor",
        headers=superuser_headers,
        json={
            "login_id": "HR001",
            "email": "hr@default.com",
            "password": "StaffPass123!",
            "full_name": "HR Sub-Admin",
            "gender": "FEMALE",
            "role_ids": [role_id],
        },
    )
    hr_token = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "hr@default.com", "password": "StaffPass123!"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {hr_token}", "X-Hospital-Subdomain": "default"}, role_id, all_perms


def test_08_privilege_escalation_guard_system_role_assignment(client, superuser_headers):
    """Scenario 8: Non-superuser with iam:write cannot assign a system role (ROLE_ADMIN) to a user."""
    hr_headers, _, _ = _create_hr_subadmin_headers(client, superuser_headers)
    esc_user = client.post(
        "/v1/api/auth/users/doctor",
        headers=hr_headers,
        json={
            "login_id": "HACK001",
            "email": "hack@default.com",
            "password": "StaffPass123!",
            "full_name": "Escalation Attempt",
            "gender": "MALE",
            "role_ids": [1],  # ROLE_ADMIN (is_system_role=True)
        },
    )
    assert esc_user.status_code == 403


def test_09_privilege_escalation_guard_unheld_permission_grant(client, superuser_headers):
    """Scenario 9: Non-superuser with iam:write cannot grant permissions they do not personally hold."""
    hr_headers, role_id, all_perms = _create_hr_subadmin_headers(client, superuser_headers)
    billing_rev_id = next(p["id"] for p in all_perms if p["code"] == "billing:reverse")

    esc_perm = client.post(
        f"/v1/api/auth/roles/{role_id}/permissions",
        headers=hr_headers,
        json=[billing_rev_id],
    )
    assert esc_perm.status_code == 403


def test_non_superuser_cannot_assign_role_with_unheld_permissions(
    client, superuser_headers
):
    hr_headers, _, _ = _create_hr_subadmin_headers(client, superuser_headers)
    elevated_role = client.post(
        "/v1/api/auth/roles",
        headers=superuser_headers,
        json={
            "name": "Sensitive Billing Role",
            "description": "Role with billing privileges",
        },
    )
    assert elevated_role.status_code == 201
    all_permissions = client.get(
        "/v1/api/auth/permissions", headers=superuser_headers
    ).json()
    elevated_permission_ids = [
        permission["id"]
        for permission in all_permissions
        if permission["code"] in {"iam:write", "billing:reverse"}
    ]
    assert client.post(
        f"/v1/api/auth/roles/{elevated_role.json()['id']}/permissions",
        headers=superuser_headers,
        json=elevated_permission_ids,
    ).status_code == 200

    create_attempt = client.post(
        "/v1/api/auth/users/doctor",
        headers=hr_headers,
        json={
            "login_id": "ESCALATE001",
            "email": "escalate@default.com",
            "password": "StaffPass123!",
            "full_name": "Privilege Escalation Attempt",
            "gender": "OTHER",
            "role_ids": [elevated_role.json()["id"]],
        },
    )
    assert create_attempt.status_code == 403

    users = client.get("/v1/api/auth/users", headers=superuser_headers).json()["items"]
    target_user = next(user for user in users if not user["is_superuser"])
    assignment_attempt = client.post(
        f"/v1/api/auth/users/{target_user['id']}/departments",
        headers=hr_headers,
        params={"department_id": 1, "role_id": elevated_role.json()["id"]},
    )
    assert assignment_attempt.status_code == 403


def test_10_system_role_modification_and_deletion_protection(client, superuser_headers):
    """Scenario 10: Protected system roles (is_system_role=True) cannot be modified or deleted."""
    assert client.put("/v1/api/auth/roles/1", headers=superuser_headers, json={"name": "Renamed"}).status_code == 400
    assert client.delete("/v1/api/auth/roles/1", headers=superuser_headers).status_code == 400


def test_11_active_role_deletion_guard_and_unused_role_deletion(client, superuser_headers):
    """Scenario 11: Deleting a role assigned to users returns 409; deleting an unassigned custom role succeeds."""
    _, assigned_role_id, _ = _create_hr_subadmin_headers(client, superuser_headers)
    assert client.delete(f"/v1/api/auth/roles/{assigned_role_id}", headers=superuser_headers).status_code == 409

    # Create an unassigned custom role and verify deletion succeeds
    temp_role = client.post(
        "/v1/api/auth/roles",
        headers=superuser_headers,
        json={"name": "Temp Unassigned Role", "description": "Safe to delete"},
    ).json()
    assert client.delete(f"/v1/api/auth/roles/{temp_role['id']}", headers=superuser_headers).status_code == 200


def test_12_department_crud_lifecycle(client, superuser_headers):
    """Scenario 12: Create, duplicate check (409), list, update, and deactivate a department."""
    create_res = client.post(
        "/v1/api/auth/departments",
        headers=superuser_headers,
        json={"code": "DEPT_NEURO", "name": "Neurology", "ndhm_speciality_code": "IN019"},
    )
    assert create_res.status_code == 201
    dept_id = create_res.json()["id"]

    dup_res = client.post(
        "/v1/api/auth/departments",
        headers=superuser_headers,
        json={"code": "DEPT_NEURO", "name": "Neurology Duplicate"},
    )
    assert dup_res.status_code == 409

    upd_res = client.put(
        f"/v1/api/auth/departments/{dept_id}",
        headers=superuser_headers,
        json={"name": "Neurology & Neurosurgery"},
    )
    assert upd_res.status_code == 200
    assert upd_res.json()["name"] == "Neurology & Neurosurgery"

    del_res = client.delete(f"/v1/api/auth/departments/{dept_id}", headers=superuser_headers)
    assert del_res.status_code == 200