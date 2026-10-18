from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.domains.identity.auth_router import _totp_code
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
    token = sent["body"].splitlines()[2]

    payload = {"token": token, "new_password": "ResetAdminPassword456!"}
    reset = client.post("/v1/api/auth/password/reset", json=payload)
    assert reset.status_code == 200, reset.text
    assert client.post("/v1/api/auth/password/reset", json=payload).status_code == 400
    assert _login(client, "ResetAdminPassword456!").status_code == 200


def test_privileged_login_requires_mfa_then_challenges_for_each_login(
    client, multi_tenant_env, monkeypatch
):
    monkeypatch.setattr(settings, "MFA_REQUIRED_FOR_PRIVILEGED", True)
    login = _login(client)
    assert login.status_code == 200
    challenge = login.json()
    assert challenge["mfa_setup_required"] is True
    assert "access_token" not in challenge

    setup = client.post(
        "/v1/api/auth/mfa/setup",
        headers={"X-Hospital-Subdomain": "default"},
        json={"mfa_token": challenge["mfa_token"]},
    )
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
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
