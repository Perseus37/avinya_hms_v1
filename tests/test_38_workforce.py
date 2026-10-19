from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domains.identity.models import Role, User
from backend.domains.workforce.models import (
    AttendanceRecord,
    LeaveRequest,
    PayrollCalculationEvent,
    PayrollLine,
    PayrollPeriod,
    SalaryProfile,
)


def _staff_id(db: Session) -> int:
    user = db.execute(
        select(User).where(User.email == "doctor@default.com")
    ).scalar_one()
    return user.id


def test_attendance_records_are_unique_by_staff_and_work_date(
    client, multi_tenant_env, superuser_headers, doctor_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        user_id = _staff_id(db)

    response = client.post(
        "/v1/api/workforce/attendance",
        headers=superuser_headers,
        json={
            "user_id": user_id,
            "work_date": "2026-10-18",
            "status": "PRESENT",
            "check_in": "08:00:00",
            "check_out": "17:00:00",
            "note": "Day shift",
        },
    )
    assert response.status_code == 201, response.text
    duplicate = client.post(
        "/v1/api/workforce/attendance",
        headers=superuser_headers,
        json={"user_id": user_id, "work_date": "2026-10-18", "status": "ABSENT"},
    )
    assert duplicate.status_code == 409
    assert client.post(
        "/v1/api/workforce/attendance",
        headers=doctor_headers,
        json={"user_id": user_id, "work_date": "2026-10-19", "status": "PRESENT"},
    ).status_code == 403
    with Session(engine) as db:
        record = db.query(AttendanceRecord).one()
        assert record.check_in == time(8, 0)
        assert record.created_by_id is not None


def test_leave_request_approval_overlap_and_self_approval(
    client, multi_tenant_env, superuser_headers, doctor_headers, auditor_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        user_id = _staff_id(db)
    payload = {
        "user_id": user_id,
        "leave_type": "CASUAL",
        "start_date": "2026-11-03",
        "end_date": "2026-11-04",
        "reason": "Family responsibility",
    }
    created = client.post(
        "/v1/api/workforce/leave-requests", headers=superuser_headers, json=payload
    )
    assert created.status_code == 201, created.text
    assert client.post(
        "/v1/api/workforce/leave-requests",
        headers=superuser_headers,
        json={**payload, "start_date": "2026-11-04", "end_date": "2026-11-05"},
    ).status_code == 409

    request_id = created.json()["id"]
    self_decision = client.patch(
        f"/v1/api/workforce/leave-requests/{request_id}/decision",
        headers=doctor_headers,
        json={"status": "APPROVED", "decision_notes": "I approve my leave"},
    )
    assert self_decision.status_code == 403
    approved = client.patch(
        f"/v1/api/workforce/leave-requests/{request_id}/decision",
        headers=auditor_headers,
        json={"status": "APPROVED", "decision_notes": "Manager-approved request"},
    )
    assert approved.status_code == 403
    approved = client.patch(
        f"/v1/api/workforce/leave-requests/{request_id}/decision",
        headers=superuser_headers,
        json={"status": "APPROVED", "decision_notes": "Manager-approved request"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    assert client.patch(
        f"/v1/api/workforce/leave-requests/{request_id}/decision",
        headers=superuser_headers,
        json={"status": "REJECTED", "decision_notes": "Changed decision"},
    ).status_code == 409
    with Session(engine) as db:
        leave = db.get(LeaveRequest, request_id)
        assert leave.decided_by_user_id is not None
        assert leave.decided_at is not None


def test_payroll_calculation_is_snapshotted_and_finalized_period_is_immutable(
    client, multi_tenant_env, superuser_headers, auditor_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        staff_id = _staff_id(db)

    profile = client.post(
        "/v1/api/workforce/salary-profiles",
        headers=superuser_headers,
        json={
            "user_id": staff_id,
            "monthly_fixed_paise": 3_000_000,
            "effective_from": "2026-10-01",
            "note": "Approved monthly salary profile",
        },
    )
    assert profile.status_code == 201, profile.text
    assert client.get(
        f"/v1/api/workforce/salary-profiles?user_id={staff_id}",
        headers=auditor_headers,
    ).status_code == 403
    assert client.post(
        "/v1/api/workforce/salary-profiles",
        headers=superuser_headers,
        json={
            "user_id": staff_id,
            "monthly_fixed_paise": 3_200_000,
            "effective_from": "2026-10-01",
            "note": "Duplicate effective date",
        },
    ).status_code == 409

    period = client.post(
        "/v1/api/workforce/payroll-periods",
        headers=superuser_headers,
        json={"period_key": "2026-10", "working_days": 20},
    )
    assert period.status_code == 201, period.text
    period_id = period.json()["id"]
    line_payload = {
        "user_id": staff_id,
        "paid_days": "10.00",
        "additional_earnings_paise": 10_000,
        "deductions_paise": 5_000,
        "calculation_note": "Paid days and adjustments verified against source records",
    }
    line = client.post(
        f"/v1/api/workforce/payroll-periods/{period_id}/lines",
        headers=superuser_headers,
        json=line_payload,
    )
    assert line.status_code == 201, line.text
    assert line.json()["prorated_fixed_paise"] == 1_500_000
    assert line.json()["gross_paise"] == 1_510_000
    assert line.json()["net_paise"] == 1_505_000

    revised = client.post(
        f"/v1/api/workforce/payroll-periods/{period_id}/lines",
        headers=superuser_headers,
        json={**line_payload, "paid_days": "12.50"},
    )
    assert revised.status_code == 201, revised.text
    line_id = revised.json()["id"]
    history = client.get(
        f"/v1/api/workforce/payroll-lines/{line_id}/history",
        headers=superuser_headers,
    )
    assert len(history.json()["items"]) == 2

    self_finalization = client.post(
        f"/v1/api/workforce/payroll-periods/{period_id}/finalize",
        headers=superuser_headers,
    )
    assert self_finalization.status_code == 403

    with Session(engine) as db:
        original_admin = db.execute(
            select(User).where(User.email == "admin@default.com")
        ).scalar_one()
        reviewer = User(
            login_id="PAYROLL_REVIEWER",
            email="payroll-reviewer@default.com",
            full_name="Payroll Reviewer",
            hashed_password=original_admin.hashed_password,
            roles=[db.execute(select(Role).where(Role.code == "ROLE_ADMIN")).scalar_one()],
        )
        db.add(reviewer)
        db.commit()
    reviewer_login = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "email": "payroll-reviewer@default.com",
            "password": "AdminPassword123!",
        },
    )
    assert reviewer_login.status_code == 200
    reviewer_headers = {
        "Authorization": f"Bearer {reviewer_login.json()['access_token']}",
        "X-Hospital-Subdomain": "default",
    }
    finalized = client.post(
        f"/v1/api/workforce/payroll-periods/{period_id}/finalize",
        headers=reviewer_headers,
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "FINALIZED"
    assert client.post(
        f"/v1/api/workforce/payroll-periods/{period_id}/lines",
        headers=superuser_headers,
        json=line_payload,
    ).status_code == 409
    assert client.post(
        f"/v1/api/workforce/payroll-periods/{period_id}/finalize",
        headers=superuser_headers,
    ).status_code == 409

    with Session(engine) as db:
        assert db.query(SalaryProfile).count() == 1
        assert db.query(PayrollPeriod).one().finalized_by_user_id is not None
        assert db.query(PayrollLine).one().net_paise == 1_880_000
        assert db.query(PayrollCalculationEvent).count() == 2


def test_payroll_rejects_future_salary_profile_and_invalid_net_amount(
    client, multi_tenant_env, superuser_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        staff_id = _staff_id(db)

    period = client.post(
        "/v1/api/workforce/payroll-periods",
        headers=superuser_headers,
        json={"period_key": "2026-09", "working_days": 22},
    )
    assert period.status_code == 201
    period_id = period.json()["id"]
    assert client.post(
        f"/v1/api/workforce/payroll-periods/{period_id}/lines",
        headers=superuser_headers,
        json={
            "user_id": staff_id,
            "paid_days": "1",
            "calculation_note": "No salary profile",
        },
    ).status_code == 409
    profile = client.post(
        "/v1/api/workforce/salary-profiles",
        headers=superuser_headers,
        json={
            "user_id": staff_id,
            "monthly_fixed_paise": 1000,
            "effective_from": "2026-10-01",
            "note": "Effective after the selected period",
        },
    )
    assert profile.status_code == 201
    later_period = client.post(
        "/v1/api/workforce/payroll-periods",
        headers=superuser_headers,
        json={"period_key": "2026-11", "working_days": 22},
    )
    assert later_period.status_code == 201
    assert client.post(
        f"/v1/api/workforce/payroll-periods/{later_period.json()['id']}/lines",
        headers=superuser_headers,
        json={
            "user_id": staff_id,
            "paid_days": "1",
            "deductions_paise": 1000,
            "calculation_note": "Deductions exceed gross",
        },
    ).status_code == 422
