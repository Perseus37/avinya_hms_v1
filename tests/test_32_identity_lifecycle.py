from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.security.totp import totp_code as _totp_code
from backend.domains.identity.models import AuthSession


def _login(client, password="AdminPassword123!"):
    return client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "admin@default.com", "password": password},
    )


def test_logout_revokes_every_access_session(client, multi_tenant_env):
    first = _login(client)
    second = _login(client)
    token = first.json()["access_token"]
    assert client.get(
        "/v1/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200

    logout = client.post(
        "/v1/api/auth/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert logout.status_code == 200
    for response in (first, second):
        assert client.get(
            "/v1/api/auth/me",
            headers={"Authorization": f"Bearer {response.json()['access_token']}"},
        ).status_code == 401

    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        assert db.query(AuthSession).filter(AuthSession.revoked_at.is_(None)).count() == 0


def test_password_change_revokes_sessions_and_updates_credentials(client):
    login = _login(client)
    token = login.json()["access_token"]
    response = client.post(
        "/v1/api/auth/password/change",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "AdminPassword123!",
            "new_password": "NewAdminPassword456!",
        },
    )
    assert response.status_code == 200, response.text
    assert client.get(
        "/v1/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401
    assert _login(client, "NewAdminPassword456!").status_code == 200


def test_email_password_reset_token_is_single_use(client, monkeypatch):
    sent = {}

    def capture_email(recipient, subject, text_body, **kwargs):
        sent.update(recipient=recipient, body=text_body)

    monkeypatch.setattr(
        "backend.domains.identity.auth_router.send_email", capture_email
    )
    request_response = client.post(
        "/v1/api/auth/password/forgot",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "admin@default.com"},
    )
    assert request_response.status_code == 202
    assert sent["recipient"] == "admin@default.com"
    reset_link = sent["body"].splitlines()[2]
    parsed_link = urlsplit(reset_link)
    assert parsed_link.path.endswith("/api/auth/password/reset")
    assert parsed_link.query == ""
    token = parsed_link.fragment.removeprefix("token=")
    page = client.get(parsed_link.path + "#" + parsed_link.fragment)
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "script-src 'nonce-" in page.headers["content-security-policy"]
    assert token not in page.text

    payload = {"token": token, "new_password": "ResetAdminPassword456!"}
    reset = client.post("/v1/api/auth/password/reset", json=payload)
    assert reset.status_code == 200, reset.text
    assert client.post("/v1/api/auth/password/reset", json=payload).status_code == 400
    assert _login(client, "ResetAdminPassword456!").status_code == 200


def _request_mfa_enrollment_code(client, mfa_token, monkeypatch):
    sent = {}

    def capture_email(recipient, subject, text_body, **kwargs):
        sent.update(recipient=recipient, body=text_body)

    monkeypatch.setattr(
        "backend.domains.identity.auth_router.send_email", capture_email
    )
    response = client.post(
        "/v1/api/auth/mfa/enrollment/request",
        headers={"X-Hospital-Subdomain": "default"},
        json={"mfa_token": mfa_token},
    )
    assert response.status_code == 202, response.text
    assert sent["recipient"] == "admin@default.com"
    return sent["body"].split(
        "one-time code to enroll your authenticator: ", 1
    )[1].splitlines()[0]


def test_privileged_login_requires_mfa_then_challenges_for_each_login(
    client, multi_tenant_env, monkeypatch
):
    monkeypatch.setattr(settings, "MFA_REQUIRED_FOR_PRIVILEGED", True)
    login = _login(client)
    assert login.status_code == 200
    challenge = login.json()
    assert challenge["mfa_setup_required"] is True
    assert "access_token" not in challenge

    enrollment_code = _request_mfa_enrollment_code(
        client, challenge["mfa_token"], monkeypatch
    )
    setup = client.post(
        "/v1/api/auth/mfa/setup",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": challenge["mfa_token"],
            "enrollment_code": enrollment_code,
        },
    )
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    replayed_enrollment = client.post(
        "/v1/api/auth/mfa/setup",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": challenge["mfa_token"],
            "enrollment_code": enrollment_code,
        },
    )
    assert replayed_enrollment.status_code == 401
    enrolled = client.post(
        "/v1/api/auth/mfa/confirm",
        headers={"X-Hospital-Subdomain": "default"},
        json={"mfa_token": challenge["mfa_token"], "code": _totp_code(secret)},
    )
    assert enrolled.status_code == 200, enrolled.text
    access_token = enrolled.json()["access_token"]
    assert len(enrolled.json()["recovery_codes"]) == 10
    assert client.get(
        "/v1/api/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    ).status_code == 200

    next_login = _login(client)
    assert next_login.status_code == 200
    assert next_login.json()["mfa_required"] is True
    same_counter = client.post(
        "/v1/api/auth/mfa/verify-login",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": next_login.json()["mfa_token"],
            "code": _totp_code(secret),
        },
    )
    assert same_counter.status_code == 401
    verified = client.post(
        "/v1/api/auth/mfa/verify-login",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": next_login.json()["mfa_token"],
            "code": enrolled.json()["recovery_codes"][0],
        },
    )
    assert verified.status_code == 200, verified.text
    assert "access_token" in verified.json()

    replay = _login(client)
    rejected_recovery = client.post(
        "/v1/api/auth/mfa/verify-login",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": replay.json()["mfa_token"],
            "code": enrolled.json()["recovery_codes"][0],
        },
    )
    assert rejected_recovery.status_code == 401
    verified_with_totp = client.post(
        "/v1/api/auth/mfa/verify-login",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": replay.json()["mfa_token"],
            "code": _totp_code(secret, datetime.now(timezone.utc) + timedelta(seconds=30)),
        },
    )
    assert verified_with_totp.status_code == 200


def test_mfa_challenge_token_cannot_access_authenticated_routes(client, monkeypatch):
    monkeypatch.setattr(settings, "MFA_REQUIRED_FOR_PRIVILEGED", True)
    challenge = _login(client).json()["mfa_token"]

    for path in ("/v1/api/auth/me", "/v1/api/auth/users"):
        response = client.get(path, headers={"Authorization": f"Bearer {challenge}"})
        assert response.status_code == 401


def test_failed_mfa_codes_lock_account_and_rate_limit_verification(
    client, multi_tenant_env, monkeypatch
):
    monkeypatch.setattr(settings, "MFA_REQUIRED_FOR_PRIVILEGED", True)
    enrollment = _login(client).json()
    enrollment_code = _request_mfa_enrollment_code(
        client, enrollment["mfa_token"], monkeypatch
    )
    setup = client.post(
        "/v1/api/auth/mfa/setup",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": enrollment["mfa_token"],
            "enrollment_code": enrollment_code,
        },
    )
    assert setup.status_code == 200
    confirmed = client.post(
        "/v1/api/auth/mfa/confirm",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": enrollment["mfa_token"],
            "code": _totp_code(setup.json()["secret"]),
        },
    )
    assert confirmed.status_code == 200

    login = _login(client).json()
    for _ in range(5):
        response = client.post(
            "/v1/api/auth/mfa/verify-login",
            headers={"X-Hospital-Subdomain": "default"},
            json={"mfa_token": login["mfa_token"], "code": "invalid"},
        )
        assert response.status_code == 401

    tenant_engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(tenant_engine) as db:
        from backend.domains.identity.models import User, UserStatusEnum

        admin = db.query(User).filter_by(email="admin@default.com").one()
        assert admin.status == UserStatusEnum.LOCKED
        assert admin.failed_login_attempts == 5

    limited = client.post(
        "/v1/api/auth/mfa/verify-login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"mfa_token": login["mfa_token"], "code": "invalid"},
    )
    assert limited.status_code == 401


def test_mfa_rate_limit_does_not_bypass_tenant_lockout_threshold(
    client, multi_tenant_env, monkeypatch
):
    from backend.domains.identity.models import User, UserStatusEnum
    from backend.domains.policies.router import get_or_create_policy

    monkeypatch.setattr(settings, "MFA_REQUIRED_FOR_PRIVILEGED", True)
    enrollment = _login(client).json()
    enrollment_code = _request_mfa_enrollment_code(
        client, enrollment["mfa_token"], monkeypatch
    )
    setup = client.post(
        "/v1/api/auth/mfa/setup",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": enrollment["mfa_token"],
            "enrollment_code": enrollment_code,
        },
    )
    assert setup.status_code == 200
    confirmed = client.post(
        "/v1/api/auth/mfa/confirm",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": enrollment["mfa_token"],
            "code": _totp_code(setup.json()["secret"]),
        },
    )
    assert confirmed.status_code == 200

    tenant_engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(tenant_engine) as db:
        policy = get_or_create_policy(db)
        policy.max_failed_login_attempts = 10
        db.commit()

    login = _login(client).json()
    for attempt in range(5):
        response = client.post(
            "/v1/api/auth/mfa/verify-login",
            headers={"X-Hospital-Subdomain": "default"},
            json={"mfa_token": login["mfa_token"], "code": "invalid"},
        )
        assert response.status_code == 401, f"attempt {attempt + 1}: {response.text}"

    valid_code = client.post(
        "/v1/api/auth/mfa/verify-login",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": login["mfa_token"],
            "code": _totp_code(
                setup.json()["secret"],
                datetime.now(timezone.utc) + timedelta(seconds=30),
            ),
        },
    )
    assert valid_code.status_code == 200, valid_code.text

    second_login = _login(client).json()
    for attempt in range(60):
        response = client.post(
            "/v1/api/auth/mfa/verify-login",
            headers={"X-Hospital-Subdomain": "default"},
            json={"mfa_token": second_login["mfa_token"], "code": "invalid"},
        )
        if attempt < 10:
            assert response.status_code == 429, f"attempt {attempt + 1}: {response.text}"
        else:
            assert response.status_code == 401, f"attempt {attempt + 1}: {response.text}"

    with Session(tenant_engine) as db:
        user = db.query(User).filter_by(email="admin@default.com").one()
        assert user.status == UserStatusEnum.LOCKED
        assert user.failed_login_attempts == 10

    valid_after_lockout = client.post(
        "/v1/api/auth/mfa/verify-login",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": second_login["mfa_token"],
            "code": _totp_code(setup.json()["secret"]),
        },
    )
    assert valid_after_lockout.status_code == 401


def test_password_only_admin_cannot_claim_initial_mfa_enrollment(
    client, multi_tenant_env, monkeypatch
):
    monkeypatch.setattr(settings, "MFA_REQUIRED_FOR_PRIVILEGED", True)
    challenge = _login(client).json()
    assert challenge["mfa_setup_required"] is True
    assert "access_token" not in challenge

    rejected_setup = client.post(
        "/v1/api/auth/mfa/setup",
        headers={"X-Hospital-Subdomain": "default"},
        json={
            "mfa_token": challenge["mfa_token"],
            "enrollment_code": "A" * 43,
        },
    )
    assert rejected_setup.status_code == 401
    with Session(multi_tenant_env["tenant_engines"]["hms_default"]) as db:
        from backend.domains.identity.models import User

        admin = db.query(User).filter_by(email="admin@default.com").one()
        assert admin.mfa_secret is None
        assert admin.mfa_enabled is False
    assert client.get("/v1/api/auth/me").status_code == 401


def test_mfa_failure_rate_limit_is_per_account_and_only_charges_failures():
    from starlette.requests import Request
    from backend.core.dependencies import RateLimiter

    class MemoryRedis:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, ex=None):
            self.values[key] = value

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/mfa/verify-login",
            "headers": [(b"host", b"localhost")],
            "query_string": b"",
            "server": ("localhost", 443),
            "client": ("192.0.2.10", 1234),
            "scheme": "https",
        }
    )
    redis = MemoryRedis()
    limiter = RateLimiter("auth_mfa_failure", max_requests=2, window_seconds=300)
    limiter.check_actor(request, redis, "user:1")
    limiter.check_actor(request, redis, "user:1")
    limiter.check_actor(request, redis, "user:2")
    with pytest.raises(HTTPException) as exc_info:
        limiter.check_actor(request, redis, "user:1")
    assert exc_info.value.status_code == 429


def test_admin_can_unlock_locked_user(client, multi_tenant_env, superuser_headers, nurse_headers):
    # F-13: Dedicated admin unlock endpoint clears lockout and resets attempts
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    from backend.domains.identity.models import User, UserStatusEnum

    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        doctor.status = UserStatusEnum.LOCKED
        doctor.failed_login_attempts = 5
        doctor.locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
        db.commit()
        doctor_id = doctor.id

    # Nurse (non-admin active user without iam:write) trying to unlock should fail with 403
    unauthorized = client.post(
        f"/v1/api/auth/users/{doctor_id}/unlock",
        headers=nurse_headers,
    )
    assert unauthorized.status_code == 403

    # Admin with iam:write permission unlocks doctor
    unlock_res = client.post(
        f"/v1/api/auth/users/{doctor_id}/unlock",
        headers=superuser_headers,
    )
    assert unlock_res.status_code == 200, unlock_res.text
    assert unlock_res.json()["status"] == "success"

    # Verify user state in database
    with Session(engine) as db:
        updated_doc = db.get(User, doctor_id)
        assert updated_doc.status == UserStatusEnum.ACTIVE
        assert updated_doc.failed_login_attempts == 0
        assert updated_doc.locked_until is None

    # N-08: Attempting to unlock an already active (non-locked) user must return 400
    active_unlock = client.post(
        f"/v1/api/auth/users/{doctor_id}/unlock",
        headers=superuser_headers,
    )
    assert active_unlock.status_code == 400
    assert "not currently locked" in active_unlock.json()["message"]
