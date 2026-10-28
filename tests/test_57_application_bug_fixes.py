"""
Tests for Application Bug Fixes & Enhancements:
1. Session persistence across browser reloads in onboarding-ui using sessionStorage.
2. Immediate fleet data synchronization on mount when restoring an existing session.
3. Local development fallback for Static SPA mounting in backend/control_plane/app.py.
4. Multi-tenant JWT authorization in Swagger UI / API docs (automatic tenant extraction from Bearer token).
5. UserLogin schema accepting hospital_slug for multi-tenant testing in API docs.
6. Standard OAuth2 password flow token endpoint (/api/auth/token and /v1/api/auth/token).
7. Swagger UI persistAuthorization configuration.
8. Addition of all 27 missing granular permissions to seed.py.
9. Exception-safe session handling in provisioning router.
"""

from pathlib import Path
import jwt
import pytest
from sqlalchemy.orm import Session
from starlette.requests import Request

from backend.core.config import settings
from backend.core.database import extract_subdomain_slug, tenant_engine_pool
from backend.core.seed import GRANULAR_PERMISSIONS, ensure_permissions_exist
from backend.domains.identity.models import Permission
from backend.main import app, v1_app


def test_subdomain_extraction_from_bearer_jwt():
    """
    Verifies that when a client (e.g. Swagger UI or automated SDK) sends an
    Authorization: Bearer <token> header on localhost without an explicit
    X-Hospital-Subdomain header, extract_subdomain_slug extracts tenant_slug
    from the JWT claims.
    """
    token = jwt.encode(
        {"sub": "1", "tenant_slug": "city-general", "jti": "mock-jti"},
        "test-secret-key-32-bytes-long-for-hmac",
        algorithm="HS256",
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/patients",
        "headers": [
            (b"host", b"localhost:8000"),
            (b"authorization", f"Bearer {token}".encode("utf-8")),
        ],
    }
    request = Request(scope)
    resolved_slug = extract_subdomain_slug(request)
    assert resolved_slug == "city-general"


def test_subdomain_extraction_header_priority():
    """
    Verifies that an explicit X-Hospital-Subdomain header takes priority over
    token claims in development/testing environments.
    """
    token = jwt.encode(
        {"sub": "1", "tenant_slug": "token-slug", "jti": "mock-jti"},
        "test-secret-key-32-bytes-long-for-hmac",
        algorithm="HS256",
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/patients",
        "headers": [
            (b"host", b"localhost:8000"),
            (b"x-hospital-subdomain", b"header-priority-slug"),
            (b"authorization", f"Bearer {token}".encode("utf-8")),
        ],
    }
    request = Request(scope)
    resolved_slug = extract_subdomain_slug(request)
    assert resolved_slug == "header-priority-slug"


def test_swagger_ui_persist_authorization_enabled():
    """
    Verifies that FastAPI's Swagger UI parameters have persistAuthorization: True
    configured on both the root app and mounted v1_app.
    """
    assert app.swagger_ui_parameters is not None
    assert app.swagger_ui_parameters.get("persistAuthorization") is True

    assert v1_app.swagger_ui_parameters is not None
    assert v1_app.swagger_ui_parameters.get("persistAuthorization") is True


def test_granular_permissions_contain_required_domain_codes(multi_tenant_env):
    """
    Verifies that all 27 previously missing permissions are present in GRANULAR_PERMISSIONS
    and get persisted into the database via ensure_permissions_exist.
    """
    code_set = {p[0] for p in GRANULAR_PERMISSIONS}
    required_codes = [
        "admin:write",
        "analytics:read",
        "auditor:read",
        "billing:pay",
        "billing:void",
        "clinical:read",
        "clinical:write",
        "emr:read",
        "emr:write",
        "executive_bi:read",
        "finance:approve",
        "finance:pay",
        "finance:write",
        "insurance:read",
        "insurance:write",
        "integrations:read",
        "integrations:write",
        "ipd:read",
        "ipd:write",
        "nursing:read",
        "nursing:write",
        "pharmacy:dispense",
        "service_ops:read",
        "service_ops:write",
        "surgery:write",
        "system:admin",
        "system:execute",
    ]
    for code in required_codes:
        assert code in code_set, f"Permission {code} missing from GRANULAR_PERMISSIONS"

    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        perm_map = ensure_permissions_exist(db)
        for code in required_codes:
            assert code in perm_map, f"Permission {code} was not seeded in database"
            db_perm = db.query(Permission).filter(Permission.code == code).first()
            assert db_perm is not None


def test_oauth2_password_flow_token_endpoint(client, multi_tenant_env):
    """
    Verifies that POST /api/auth/token accepts OAuth2 standard password credentials
    (x-www-form-urlencoded) and returns a valid Bearer token.
    """
    res = client.post(
        "/api/auth/token",
        data={
            "username": "admin@default.com",
            "password": "AdminPassword123!",
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Hospital-Subdomain": "default",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert "access_token" in data
    assert data["token_type"].lower() == "bearer"


def test_login_with_explicit_hospital_slug(client, multi_tenant_env):
    """
    Verifies that POST /api/auth/login accepts an optional hospital_slug field in
    the JSON body without breaking standard login flows.
    """
    res = client.post(
        "/api/auth/login",
        json={
            "email": "admin@apollo.com",
            "password": "AdminPassword123!",
            "hospital_slug": "apollo",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert "access_token" in data
    assert data["token_type"].lower() == "bearer"


def test_control_plane_spa_dist_resolution():
    """
    Verifies that backend/control_plane/app.py resolves SPA_DIST dynamically
    from repository root if /app/onboarding-ui/dist does not exist.
    """
    from backend.control_plane.app import LOCAL_SPA_DIST, SPA_DIST
    assert LOCAL_SPA_DIST.name == "dist"
    assert LOCAL_SPA_DIST.parent.name == "onboarding-ui"
    assert Path(SPA_DIST).exists(), f"Resolved SPA_DIST '{SPA_DIST}' does not exist on disk"


def test_onboarding_ui_session_persistence_contract():
    """
    Verifies frontend code contracts:
    - onboarding-ui/src/App.jsx imports and reads from sessionStorage.
    - onboarding-ui/src/hooks/useFleetData.js sets previousOperatorToken to false on mount.
    """
    app_jsx = Path("onboarding-ui/src/App.jsx").read_text(encoding="utf-8")
    assert "sessionStorage.getItem(OPERATOR_SESSION_KEY)" in app_jsx
    assert "sessionStorage.setItem(OPERATOR_SESSION_KEY" in app_jsx
    assert "sessionStorage.removeItem(OPERATOR_SESSION_KEY)" in app_jsx

    fleet_hook = Path("onboarding-ui/src/hooks/useFleetData.js").read_text(encoding="utf-8")
    assert "const previousOperatorToken = useRef(false);" in fleet_hook
    assert "!lastSyncedAt && !syncing.current" in fleet_hook
