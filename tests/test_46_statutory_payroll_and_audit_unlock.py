"""Tests for M-05 Statutory Indian payroll deductions, payslip generation, and N-08 administrative unlock audit logging."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.domains.identity.models import User, UserStatusEnum
from backend.domains.privacy.models import PrivacyDecisionAudit
from backend.domains.workforce.models import PayrollLine, PayrollPeriod, SalaryProfile
from backend.domains.workforce.services import calculate_indian_statutory_deductions


def test_calculate_indian_statutory_deductions_brackets():
    # 1. Low salary: Gross <= ₹21,000 (ESI applies, PF on basic, PT applies if >= ₹15,000)
    # Salary = ₹20,000 (2,000,000 paise)
    res_low = calculate_indian_statutory_deductions(
        gross_paise=2_000_000,
        prorated_fixed_paise=2_000_000,
        pf_opt_out=False,
    )
    # PF capped at ₹15,000 (1,500,000 paise) = 180,000 paise
    assert res_low["pf_employee_paise"] == 180_000
    assert res_low["pf_employer_paise"] == 180_000
    # ESI: 0.75% of 2,000,000 = 15,000 paise
    assert res_low["esi_employee_paise"] == 15_000
    assert res_low["esi_employer_paise"] == 65_000
    # PT: ₹200 (20,000 paise)
    assert res_low["professional_tax_paise"] == 20_000
    assert res_low["tds_paise"] == 0
    assert res_low["total_employee_statutory_paise"] == 180_000 + 15_000 + 20_000

    # 2. High salary: Gross > ₹21,000 (ESI exempt, PF capped at ceiling, custom TDS)
    # Salary = ₹50,000 (5,000,000 paise)
    res_high = calculate_indian_statutory_deductions(
        gross_paise=5_000_000,
        prorated_fixed_paise=5_000_000,
        pf_opt_out=False,
        custom_tds_paise=250_000,
    )
    assert res_high["pf_employee_paise"] == 180_000
    assert res_high["esi_employee_paise"] == 0
    assert res_high["esi_employer_paise"] == 0
    assert res_high["professional_tax_paise"] == 20_000
    assert res_high["tds_paise"] == 250_000
    assert res_high["total_employee_statutory_paise"] == 180_000 + 20_000 + 250_000

    # 3. PF Opt-out
    res_opt_out = calculate_indian_statutory_deductions(
        gross_paise=5_000_000,
        prorated_fixed_paise=5_000_000,
        pf_opt_out=True,
    )
    assert res_opt_out["pf_employee_paise"] == 0
    assert res_opt_out["pf_employer_paise"] == 0

    # 4. Uncapped PF
    res_uncapped = calculate_indian_statutory_deductions(
        gross_paise=3_000_000,
        prorated_fixed_paise=3_000_000,
        uncapped_pf=True,
    )
    assert res_uncapped["pf_employee_paise"] == int(3_000_000 * 0.12)


def test_statutory_payroll_api_workflow_and_payslip(
    client, multi_tenant_env, superuser_headers, nurse_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        # Create a test employee
        staff = User(
            login_id="nurse_statutory_1",
            email="nurse.statutory@example.invalid",
            full_name="Priya Sharma",
            hashed_password="dummy_hash_pw",
            status=UserStatusEnum.ACTIVE,
        )
        db.add(staff)
        db.commit()
        db.refresh(staff)
        staff_id = staff.id

    # 1. Create Salary Profile (Monthly CTC = ₹30,000 = 3,000,000 paise)
    prof_resp = client.post(
        "/v1/api/workforce/salary-profiles",
        headers=superuser_headers,
        json={
            "user_id": staff_id,
            "monthly_fixed_paise": 3_000_000,
            "effective_from": "2026-11-01",
            "note": "Standard nursing grade salary",
        },
    )
    assert prof_resp.status_code == 201, prof_resp.text

    # 2. Create Payroll Period
    period_resp = client.post(
        "/v1/api/workforce/payroll-periods",
        headers=superuser_headers,
        json={"period_key": "2026-11", "working_days": 20},
    )
    assert period_resp.status_code == 201, period_resp.text
    period_id = period_resp.json()["id"]

    # 3. Calculate Line with Indian statutory deductions
    line_payload = {
        "user_id": staff_id,
        "paid_days": "20.00",
        "additional_earnings_paise": 0,
        "deductions_paise": 0,
        "calculation_note": "Full attendance statutory calculation",
        "include_statutory_deductions": True,
        "pf_opt_out": False,
        "custom_tds_paise": 50_000,
        "pt_state": "KA",
    }
    line_resp = client.post(
        f"/v1/api/workforce/payroll-periods/{period_id}/lines",
        headers=superuser_headers,
        json=line_payload,
    )
    assert line_resp.status_code == 201, line_resp.text
    line_data = line_resp.json()
    line_id = line_data["id"]

    assert line_data["prorated_fixed_paise"] == 3_000_000
    assert line_data["gross_paise"] == 3_000_000
    assert line_data["pf_employee_paise"] == 180_000
    assert line_data["esi_employee_paise"] == 0  # > 21k gross
    assert line_data["professional_tax_paise"] == 20_000
    assert line_data["tds_paise"] == 50_000
    # Net: 3,000,000 - (180,000 + 20,000 + 50,000) = 2,750,000
    assert line_data["net_paise"] == 2_750_000

    # 4. Fetch Structured Payslip
    payslip_resp = client.get(
        f"/v1/api/workforce/payroll-lines/{line_id}/payslip",
        headers=superuser_headers,
    )
    assert payslip_resp.status_code == 200, payslip_resp.text
    payslip = payslip_resp.json()

    assert payslip["employee"]["name"] == "Priya Sharma"
    assert payslip["employee"]["email"] == "nurse.statutory@example.invalid"
    assert payslip["period"]["period_key"] == "2026-11"
    assert payslip["earnings"]["gross_formatted"] == "₹30,000.00"
    assert payslip["statutory_deductions"]["pf_employee_formatted"] == "₹1,800.00"
    assert payslip["statutory_deductions"]["professional_tax_formatted"] == "₹200.00"
    assert payslip["statutory_deductions"]["tds_formatted"] == "₹500.00"
    assert payslip["total_deductions_formatted"] == "₹2,500.00"
    assert payslip["net_pay_formatted"] == "₹27,500.00"
    assert payslip["employer_statutory_contributions"]["pf_employer_formatted"] == "₹1,800.00"


def test_admin_unlock_writes_audit_entry(
    client, multi_tenant_env, superuser_headers, nurse_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        locked_user = User(
            login_id="locked_staff_1",
            email="locked.staff@example.invalid",
            full_name="Locked Staff",
            hashed_password="dummy_hash_pw",
            status=UserStatusEnum.LOCKED,
            failed_login_attempts=5,
            locked_until=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(locked_user)
        db.commit()
        db.refresh(locked_user)
        user_id = locked_user.id

    # 1. Non-admin cannot unlock (403 Forbidden)
    res_forbidden = client.post(
        f"/v1/api/auth/users/{user_id}/unlock",
        headers=nurse_headers,
    )
    assert res_forbidden.status_code == 403

    # 2. Admin unlock succeeds
    res_unlock = client.post(
        f"/v1/api/auth/users/{user_id}/unlock",
        headers=superuser_headers,
    )
    assert res_unlock.status_code == 200, res_unlock.text
    assert "unlocked" in res_unlock.json()["message"].lower()

    # 3. Verify user is now active and unlocked in DB
    with Session(engine) as db:
        updated_user = db.get(User, user_id)
        assert updated_user.status == UserStatusEnum.ACTIVE
        assert updated_user.locked_until is None
        assert updated_user.failed_login_attempts == 0

        # 4. Verify N-08 Audit Log Entry
        audit = db.execute(
            select(PrivacyDecisionAudit).where(
                PrivacyDecisionAudit.data_use_point == "identity.user.unlock",
                PrivacyDecisionAudit.result == "ALLOW",
            )
        ).scalars().first()

        assert audit is not None
        assert f"unlocked_user_id={user_id}" in audit.reason_code
        assert audit.mode == "ENFORCE"

    # 5. Unlocking already active account returns 400
    res_already_active = client.post(
        f"/v1/api/auth/users/{user_id}/unlock",
        headers=superuser_headers,
    )
    assert res_already_active.status_code == 400
    assert "not currently locked" in res_already_active.json()["message"]


def test_admin_unlock_clears_mfa_rate_limiter(
    client, multi_tenant_env, superuser_headers
):
    """N-08: Admin unlock explicitly clears the Redis rate-limiter counter so 429 stops."""
    from backend.domains.identity.models import User, UserStatusEnum

    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        locked_user = User(
            login_id="locked_mfa_user",
            email="locked.mfa@example.invalid",
            full_name="Locked MFA User",
            hashed_password="dummy_hash_pw",
            status=UserStatusEnum.LOCKED,
            failed_login_attempts=5,
            locked_until=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(locked_user)
        db.commit()
        db.refresh(locked_user)
        uid = locked_user.id

    redis_cli = multi_tenant_env["redis"]
    key = f"rl:auth_mfa_failure:default:user:{uid}"
    redis_cli.set(key, "10")
    assert int(redis_cli.get(key)) == 10

    res = client.post(
        f"/v1/api/auth/users/{uid}/unlock",
        headers=superuser_headers,
    )
    assert res.status_code == 200

    val = redis_cli.get(f"rl:auth_mfa_failure:default:user:{uid}")
    assert val is None, f"Expected key to be deleted, found: {val}"

