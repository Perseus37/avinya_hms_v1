from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.control_plane import app as control_app_module
from backend.control_plane.models import (
    ControlBase,
    ControlPlaneAuditLog,
    ControlPlaneOperator,
)
from backend.control_plane.services import auth as control_auth
from backend.core.database import get_control_db
from backend.core.dependencies import get_redis
from backend.domains.identity.passwords import pwd_context
from backend.security.totp import totp_code


def test_named_operator_mfa_login_auths_control_plane_and_logout_revokes(
    monkeypatch,
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ControlBase.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with SessionLocal() as db:
        db.add(
            ControlPlaneOperator(
                email="operator@example.com",
                full_name="Test Operator",
                role="superadmin",
                password_hash=pwd_context.hash("OperatorPass123!"),
            )
        )
        db.commit()

    def override_get_control_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    class TestRedis:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, ex=None):
            self.values[key] = value

    monkeypatch.setattr(control_app_module, "ControlSessionLocal", SessionLocal)
    monkeypatch.setattr(control_auth, "ControlSessionLocal", SessionLocal)
    control_app_module.app.dependency_overrides[get_control_db] = override_get_control_db
    control_app_module.app.dependency_overrides[get_redis] = lambda: TestRedis()

    try:
        with TestClient(control_app_module.app) as client:
            first_login = client.post(
                "/api/auth/login",
                json={"email": "operator@example.com", "password": "OperatorPass123!"},
            )
            assert first_login.status_code == 200
            challenge = first_login.json()
            assert challenge["mfa_setup_required"] is True
            assert "access_token" not in challenge

            setup = client.post(
                "/api/auth/mfa/setup", json={"mfa_token": challenge["mfa_token"]}
            )
            assert setup.status_code == 200, setup.text
            secret = setup.json()["secret"]
            confirmed = client.post(
                "/api/auth/mfa/confirm",
                json={
                    "mfa_token": challenge["mfa_token"],
                    "code": totp_code(secret),
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            access_token = confirmed.json()["access_token"]

            protected = client.get(
                "/api/pricing/catalog",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert protected.status_code == 200, protected.text
            vendor_protected = client.get(
                "/api/fleet/overview",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert vendor_protected.status_code == 200, vendor_protected.text
            vendor_body_protected = client.patch(
                "/api/tenants/operator-auth-test-missing/status",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"master_setup_key": "", "is_active": False},
            )
            assert vendor_body_protected.status_code == 404

            actor_token = control_auth.control_plane_actor.set("operator@example.com")
            try:
                with SessionLocal() as db:
                    db.add(
                        ControlPlaneAuditLog(
                            action="TEST_OPERATOR_AUDIT",
                            target_slug="CONTROL_PLANE",
                        )
                    )
                    db.commit()
            finally:
                control_auth.control_plane_actor.reset(actor_token)
            with SessionLocal() as db:
                audit = db.query(ControlPlaneAuditLog).filter_by(
                    action="TEST_OPERATOR_AUDIT"
                ).one()
                assert audit.actor == "operator@example.com"

            logout = client.post(
                "/api/auth/logout",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert logout.status_code == 200
            denied = client.get(
                "/api/pricing/catalog",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert denied.status_code == 401
    finally:
        control_app_module.app.dependency_overrides.pop(get_control_db, None)
        control_app_module.app.dependency_overrides.pop(get_redis, None)
        engine.dispose()


def test_operator_roles_and_master_key_scoping(monkeypatch):
    """Audit F-08 regression: Non-superadmin operators cannot bypass master vendor key verification."""
    from fastapi import HTTPException
    import pytest
    from backend.core.config import settings
    from backend.control_plane.services.auth import _verify_vendor_key, control_plane_operator_role

    monkeypatch.setattr(settings, "MASTER_ONBOARDING_KEY", "real-super-secret-vendor-key")

    # 1. Unauthenticated / No role caller without key is rejected
    with pytest.raises(HTTPException) as exc_info:
        _verify_vendor_key("")
    assert exc_info.value.status_code == 403

    # 2. Support role operator without key is rejected (cannot bypass)
    token = control_plane_operator_role.set("support")
    try:
        with pytest.raises(HTTPException) as exc_info:
            _verify_vendor_key("")
        assert exc_info.value.status_code == 403

        # Support operator WITH correct key succeeds
        _verify_vendor_key("real-super-secret-vendor-key")
    finally:
        control_plane_operator_role.reset(token)

    # 3. Superadmin operator can execute vendor ops
    token = control_plane_operator_role.set("superadmin")
    try:
        _verify_vendor_key("")  # Allowed for superadmin
    finally:
        control_plane_operator_role.reset(token)
