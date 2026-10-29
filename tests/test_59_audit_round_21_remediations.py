"""
Audit Round 21 Remediation Test Suite.
Verifies remediations for findings identified in external auditor report (Round 21):
1. N-75 & N-77 (CRITICAL - Control Plane Arbitrary File Read & Path Traversal Prevention):
   - Confines control-plane web app serving strictly within SPA_DIST boundaries.
   - Blocks /..%2f traversal, /%2f absolute paths, and encoded path escapes.
   - Ensures fallback to index.html or 404 without leaking server files or environment secrets.
   - Makes SPA tests independent of whether onboarding-ui/dist has been pre-built.
2. N-74 (Medium-High - ABDM Scan-and-Share Demographic Hardening & Production Guard):
   - Blocks simulated Scan & Share in production environments with HTTP 503.
   - Rejects non-JSON strings and unverified payloads with HTTP 400.
   - Strictly prohibits placeholder dummy demographics ("Scan-Share Patient", default DOB, default gender).
3. N-76 (Medium - Tenant Routing Signature Verification & Login Enumeration Defense):
   - Cryptographically verifies Bearer token signature against claimed tenant's secret key before routing.
   - In production, prevents switching hospital_slug in request body across differing host domains.
   - Unifies invalid and suspended hospital error messages to prevent tenant enumeration.
   - Guarantees database session cleanup via finally block.
"""

import jwt
from starlette.testclient import TestClient

from backend.control_plane.app import app as control_plane_app
from backend.core.config import settings
from backend.core.database import extract_subdomain_slug


# =============================================================================
# 1. N-75 & N-77: Control Plane Directory Traversal Prevention
# =============================================================================

def test_n75_n77_control_plane_path_traversal_blocked(monkeypatch, tmp_path):
    """
    CRITICAL N-75 & N-77:
    Verify that the control plane static file handler strictly forbids path traversal.
    Requests with encoded slashes (%2f), parent directories (..), or absolute paths
    must NEVER return files outside SPA_DIST.
    """
    # Create isolated web folder and an external secret file
    web_dir = tmp_path / "web_dist"
    web_dir.mkdir()
    index_file = web_dir / "index.html"
    index_content = "<!doctype html><html><body>Avinya Admin SPA</body></html>"
    index_file.write_text(index_content, encoding="utf-8")

    # Legitimate asset inside web_dir
    favicon = web_dir / "favicon.ico"
    favicon.write_text("FAVICON_BYTES", encoding="utf-8")

    # Sensitive secret file OUTSIDE web_dir
    secret_file = tmp_path / "secret_server_env.txt"
    secret_file.write_text("SUPER_SECRET_DATABASE_PASSWORD_XYZ123", encoding="utf-8")

    monkeypatch.setenv("SPA_DIST_DIR", str(web_dir))
    client = TestClient(control_plane_app)

    # 1. Legitimate SPA routes serve index.html or actual asset
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "Avinya Admin SPA" in res_root.text

    res_dash = client.get("/dashboard")
    assert res_dash.status_code == 200
    assert "Avinya Admin SPA" in res_dash.text

    res_fav = client.get("/favicon.ico")
    assert res_fav.status_code == 200
    assert res_fav.text == "FAVICON_BYTES"

    # 2. Traversal attempts must NEVER return secret_server_env.txt
    res_traversal1 = client.get("/..%2fsecret_server_env.txt")
    assert secret_file.read_text() not in res_traversal1.text
    assert res_traversal1.text == index_content

    res_traversal2 = client.get("/../secret_server_env.txt")
    assert secret_file.read_text() not in res_traversal2.text

    res_traversal3 = client.get("/..%2f..%2fsecret_server_env.txt")
    assert secret_file.read_text() not in res_traversal3.text

    # 3. Absolute path injection attempts must NEVER return system files
    res_abs1 = client.get("/%2fetc/passwd")
    assert "root:" not in res_abs1.text
    assert secret_file.read_text() not in res_abs1.text

    res_abs2 = client.get("/%2fproc/self/environ")
    assert "PATH=" not in res_abs2.text
    assert secret_file.read_text() not in res_abs2.text

    # 4. Windows drive paths
    res_win = client.get("/C:/Windows/win.ini")
    assert "[fonts]" not in res_win.text.lower()


# =============================================================================
# 2. N-74: ABDM Scan & Share Hardening
# =============================================================================

def test_n74_abdm_scan_share_production_guard(client, superuser_headers, monkeypatch):
    """
    N-74: ABDM Scan & Share in production environments strictly blocks simulated
    gateway access with HTTP 503.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    res = client.post(
        "/api/abdm/m1/scan-share",
        headers=superuser_headers,
        json={
            "counter_id": "REG-01",
            "qr_payload": '{"name": "Valid Name", "gender": "MALE", "dob": "1990-01-01T00:00:00Z", "abhaAddress": "user@abdm"}',
        },
    )
    assert res.status_code == 503
    assert "gateway" in res.text.lower()


def test_n74_abdm_scan_share_rejects_dummy_and_non_json_payloads(client, superuser_headers, monkeypatch):
    """
    N-74: ABDM Scan & Share strictly rejects non-JSON strings and fabricated placeholder demographics.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")

    # Non-JSON arbitrary typed string -> 400
    res1 = client.post(
        "/api/abdm/m1/scan-share",
        headers=superuser_headers,
        json={"counter_id": "REG-01", "qr_payload": "anyone.typed@abdm"},
    )
    assert res1.status_code == 400
    assert "valid json" in res1.text.lower()

    # Missing verified patient name -> 400
    res2 = client.post(
        "/api/abdm/m1/scan-share",
        headers=superuser_headers,
        json={"counter_id": "REG-01", "qr_payload": '{"gender": "MALE", "dob": "1990-01-01T00:00:00Z", "abhaAddress": "user@abdm"}'},
    )
    assert res2.status_code == 400

    # Placeholder dummy name "Scan-Share Patient" -> 400
    res3 = client.post(
        "/api/abdm/m1/scan-share",
        headers=superuser_headers,
        json={"counter_id": "REG-01", "qr_payload": '{"name": "Scan-Share Patient", "gender": "MALE", "dob": "1990-01-01T00:00:00Z", "abhaAddress": "user@abdm"}'},
    )
    assert res3.status_code == 400

    # Missing date of birth -> 400
    res4 = client.post(
        "/api/abdm/m1/scan-share",
        headers=superuser_headers,
        json={"counter_id": "REG-01", "qr_payload": '{"name": "Real Patient", "gender": "MALE", "abhaAddress": "user@abdm"}'},
    )
    assert res4.status_code == 400

    # Missing ABHA address -> 400
    res5 = client.post(
        "/api/abdm/m1/scan-share",
        headers=superuser_headers,
        json={"counter_id": "REG-01", "qr_payload": '{"name": "Real Patient", "gender": "MALE", "dob": "1990-01-01T00:00:00Z"}'},
    )
    assert res5.status_code == 400


def test_n74_abdm_scan_share_valid_payload_creates_real_patient(client, superuser_headers, monkeypatch):
    """
    N-74: Verified QR payload creates patient with actual received demographics, never default fallbacks.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    res = client.post(
        "/api/abdm/m1/scan-share",
        headers=superuser_headers,
        json={
            "counter_id": "REG-01",
            "qr_payload": '{"name": "Priya Sharma", "gender": "FEMALE", "dob": "1988-11-23T00:00:00Z", "abhaAddress": "priya.sharma@abdm"}',
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert "token_number" in data
    assert "patient_id" in data

    # Verify real demographics stored
    p_res = client.get(f"/v1/api/patients/{data['patient_id']}", headers=superuser_headers)
    assert p_res.status_code == 200
    patient = p_res.json()
    assert patient["full_name"] == "Priya Sharma"
    assert patient["gender"].upper() == "FEMALE"
    assert patient["abha_number"] is not None


# =============================================================================
# 3. N-76: Tenant Routing Signature Verification & Login Defenses
# =============================================================================

class DummyRequest:
    def __init__(self, headers):
        self.headers = headers


def test_n76_unverified_token_signature_rejected_for_routing(multi_tenant_env):
    """
    N-76: Bearer token claiming a tenant_slug with an invalid signature must NOT route
    to that tenant's database.
    """
    # Mint token with a forged/random secret key
    forged_token = jwt.encode(
        {"sub": "attacker", "tenant_slug": "default"},
        "INVALID_SECRET_KEY_NOT_MATCHING_DEFAULT_TENANT_32BYTES!",
        algorithm="HS256"
    )
    req = DummyRequest({"authorization": f"Bearer {forged_token}"})
    # Signature verification fails, so extract_subdomain_slug falls back to default/host
    assert extract_subdomain_slug(req) == settings.DEFAULT_TENANT_SLUG

    forged_other_tenant = jwt.encode(
        {"sub": "attacker", "tenant_slug": "apollo_hospital"},
        "INVALID_SECRET_KEY_NOT_MATCHING_APOLLO_32BYTES_LONG!",
        algorithm="HS256"
    )
    req2 = DummyRequest({"authorization": f"Bearer {forged_other_tenant}"})
    resolved_slug = extract_subdomain_slug(req2)
    # Must NOT route to apollo_hospital because token signature could not be verified
    assert resolved_slug != "apollo_hospital"
    assert resolved_slug == settings.DEFAULT_TENANT_SLUG


def test_n76_login_enumeration_defense_and_production_guard(client, monkeypatch):
    """
    N-76:
    - Attempting login with unknown or suspended hospital_slug returns uniform HTTP 400
      without leaking hospital legal names or confirming tenant existence.
    - In production, passing hospital_slug differing from host is blocked with HTTP 400.
    """
    # 1. Unknown hospital slug returns uniform 400
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    res_unknown = client.post(
        "/v1/api/auth/login",
        json={"email": "admin@hospital.org", "password": "AnyPassword123!", "hospital_slug": "non_existent_xyz"},
        headers={"X-Hospital-Subdomain": "default"}
    )
    assert res_unknown.status_code == 400
    assert "Invalid hospital subdomain specified." in res_unknown.text

    # 2. Production blocks cross-host hospital switching
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    res_prod_switch = client.post(
        "/v1/api/auth/login",
        json={"email": "admin@hospital.org", "password": "AnyPassword123!", "hospital_slug": "other_hospital"},
        headers={"X-Hospital-Subdomain": "default"}
    )
    assert res_prod_switch.status_code == 400
    assert "Hospital routing mismatch" in res_prod_switch.text
