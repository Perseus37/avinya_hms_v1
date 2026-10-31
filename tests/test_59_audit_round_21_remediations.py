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

from pathlib import Path
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


def test_n58_get_client_ip_resolves_trusted_proxies(monkeypatch):
    """
    N-58: Verifies get_client_ip extracts real client IP through trusted reverse proxies
    and ignores untrusted forwarded headers.
    """
    from backend.core.dependencies import get_client_ip
    from starlette.requests import Request

    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.0.0.1,172.16.0.0/16")

    # 1. Request directly from client (no proxy)
    req1 = Request({
        "type": "http",
        "client": ("203.0.113.195", 54321),
        "headers": [(b"host", b"hospital.example.com")],
    })
    assert get_client_ip(req1) == "203.0.113.195"

    # 2. Request through trusted proxy with X-Forwarded-For
    req2 = Request({
        "type": "http",
        "client": ("10.0.0.1", 443),
        "headers": [
            (b"host", b"hospital.example.com"),
            (b"x-forwarded-for", b"198.51.100.42, 172.16.0.5"),
        ],
    })
    assert get_client_ip(req2) == "198.51.100.42"

    # 3. Untrusted client spoofing X-Forwarded-For is ignored
    req3 = Request({
        "type": "http",
        "client": ("203.0.113.5", 54321),
        "headers": [
            (b"host", b"hospital.example.com"),
            (b"x-forwarded-for", b"198.51.100.42"),
        ],
    })
    assert get_client_ip(req3) == "203.0.113.5"


def test_prometheus_alert_rules_and_config_syntax():
    """
    Verifies that deployments/prometheus-alerts.yml and deployments/prometheus.yml
    exist, have valid YAML syntax, and contain expected alert rule thresholds and scrape jobs.
    """
    import yaml
    from pathlib import Path

    alerts_path = Path("deployments/prometheus-alerts.yml")
    assert alerts_path.is_file()
    alerts = yaml.safe_load(alerts_path.read_text(encoding="utf-8"))
    assert "groups" in alerts
    group_names = [g["name"] for g in alerts["groups"]]
    assert "avinya_hms_service_alerts" in group_names
    assert "avinya_hms_infrastructure_alerts" in group_names

    rule_names = [r["alert"] for g in alerts["groups"] for r in g["rules"]]
    assert "ApiServiceDown" in rule_names
    assert "ControlPlaneServiceDown" in rule_names
    assert "AlertmanagerDown" in rule_names
    assert "HighHttp5xxErrorRate" in rule_names
    assert "HighRequestLatencyP95" in rule_names
    assert "PostgresDown" in rule_names
    assert "RedisDown" in rule_names
    assert "BackupFreshnessWarning" in rule_names
    assert "BackupRunFailed" in rule_names

    # N-79: Verify BackupFreshnessWarning triggers on missing metric
    backup_rule = next(r for g in alerts["groups"] for r in g["rules"] if r["alert"] == "BackupFreshnessWarning")
    assert "absent(backup_last_completion_timestamp_seconds)" in backup_rule["expr"]

    prom_path = Path("deployments/prometheus.yml")
    assert prom_path.is_file()
    prom = yaml.safe_load(prom_path.read_text(encoding="utf-8"))
    assert "alerting" in prom
    assert "scrape_configs" in prom
    jobs = {job["job_name"]: job for job in prom["scrape_configs"]}
    assert "avinya-api" in jobs
    assert "avinya-onboarding" in jobs
    assert "alertmanager" in jobs
    assert "postgres" in jobs
    assert "redis" in jobs
    assert "node" in jobs
    assert "authorization" in jobs["avinya-onboarding"]

    # N-82: Verify Alertmanager configuration
    am_path = Path("deployments/alertmanager.yml")
    assert am_path.is_file()
    am = yaml.safe_load(am_path.read_text(encoding="utf-8"))
    assert "route" in am
    assert "receivers" in am
    receiver_names = [r["name"] for r in am["receivers"]]
    assert "default-receiver" in receiver_names
    assert "critical-receiver" in receiver_names


def test_control_plane_metrics_and_health_endpoint(monkeypatch):
    """
    N-83: Verify Control Plane exposes /health and token-authenticated /metrics.
    """
    from starlette.testclient import TestClient
    from backend.control_plane.app import app as cp_app

    client = TestClient(cp_app)

    # 1. Health liveness probe
    health_res = client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "healthy"

    # 2. Metrics endpoint requires token in production
    test_token = "metrics-secret-token-32-chars-long!"
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", test_token)

    # Unauthenticated / wrong token returns 401
    unauth_res = client.get("/metrics")
    assert unauth_res.status_code == 401
    assert "valid metrics scrape token required" in unauth_res.text

    wrong_res = client.get("/metrics", headers={"Authorization": "Bearer wrong-token"})
    assert wrong_res.status_code == 401

    # Valid Bearer token returns 200 with Prometheus text format
    valid_res = client.get("/metrics", headers={"Authorization": f"Bearer {test_token}"})
    assert valid_res.status_code == 200
    assert "process_cpu_seconds_total" in valid_res.text or "python_info" in valid_res.text or len(valid_res.text) > 0

def test_n85_alertmanager_static_syntax_and_renderer(tmp_path):
    """
    N-85: Alertmanager syntax must be strictly valid static YAML without raw shell ${VAR} strings.
    The renderer backend.scripts.render_alertmanager_config must substitute variables and validate URLs.
    """
    import re
    import yaml
    import pytest
    from backend.scripts.render_alertmanager_config import render_alertmanager_config

    am_path = Path("deployments/alertmanager.yml")
    assert am_path.is_file()
    raw_text = am_path.read_text(encoding="utf-8")

    # 1. Statically committed alertmanager.yml must have zero unexpanded placeholders
    placeholders = re.findall(r"\$\{[^}]+\}", raw_text)
    assert not placeholders, f"Unexpanded placeholders found in alertmanager.yml: {placeholders}"

    # 2. Must parse cleanly as YAML
    data = yaml.safe_load(raw_text)
    assert "route" in data
    assert "receivers" in data
    assert "smtp_smarthost" in data.get("global", {})
    # SMTP smarthost must have host:port format without extra colons
    smarthost = data["global"]["smtp_smarthost"]
    assert smarthost.count(":") == 1

    # 3. All receiver webhooks must have valid URLs
    for receiver in data["receivers"]:
        for webhook in receiver.get("webhook_configs", []):
            url = webhook["url"]
            assert url.startswith("http://") or url.startswith("https://")
            assert not url.startswith("${")

    # 4. Test render_alertmanager_config function with custom env file
    custom_env = tmp_path / "custom.env"
    custom_env.write_text(
        "SMTP_HOST=mail.hospital.org\n"
        "SMTP_PORT=25\n"
        "ALERT_EMAIL_FROM=noc@hospital.org\n"
        "ALERT_DEFAULT_WEBHOOK_URL=https://api.hospital.org/alerts/ingress\n",
        encoding="utf-8",
    )
    rendered_output = tmp_path / "alertmanager.rendered.yml"
    render_alertmanager_config(env_file=custom_env, output_file=rendered_output)

    rendered_data = yaml.safe_load(rendered_output.read_text(encoding="utf-8"))
    assert rendered_data["global"]["smtp_smarthost"] == "mail.hospital.org:25"
    assert rendered_data["global"]["smtp_from"] == "noc@hospital.org"
    assert rendered_data["receivers"][0]["webhook_configs"][0]["url"] == "https://api.hospital.org/alerts/ingress"
    assert rendered_data["receivers"][1]["webhook_configs"][0]["url"] == "https://api.hospital.org/alerts/ingress?severity=critical"

    # 5. Invalid URL in env file raises ValueError
    bad_env = tmp_path / "bad.env"
    bad_env.write_text("ALERT_DEFAULT_WEBHOOK_URL=not-a-valid-url\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid URL"):
        render_alertmanager_config(env_file=bad_env, output_file=rendered_output)


def test_n86_n87_compose_production_monitoring_hardening():
    """
    N-86 & N-87:
    - node-exporter must bind-mount host textfile directory via NODE_EXPORTER_TEXTFILE_HOST_PATH,
      not an isolated Docker named volume.
    - All monitoring services (prometheus, alertmanager, postgres-exporter, redis-exporter, node-exporter)
      must have security hardening: user 65534:65534, cap_drop ALL, and read_only true.
    """
    import yaml

    prod_compose_path = Path("docker-compose.prod.yml")
    assert prod_compose_path.is_file()
    compose = yaml.safe_load(prod_compose_path.read_text(encoding="utf-8"))

    # N-86: node-exporter volume
    node_exp = compose["services"]["node-exporter"]
    node_volumes = node_exp["volumes"]
    assert any("NODE_EXPORTER_TEXTFILE_HOST_PATH" in v and "/var/lib/node_exporter/textfile_collector:ro" in v for v in node_volumes)
    assert "hms_node_exporter_textfile" not in compose.get("volumes", {})

    # N-87: exporter hardening
    monitoring_services = [
        "prometheus",
        "alertmanager",
        "postgres-exporter",
        "redis-exporter",
        "node-exporter",
    ]
    for name in monitoring_services:
        svc = compose["services"][name]
        assert svc.get("user") == "65534:65534", f"{name} must run as user 65534:65534"
        assert svc.get("read_only") is True, f"{name} must have read_only: true"
        assert svc.get("cap_drop") == ["ALL"], f"{name} must have cap_drop: [ALL]"
        assert svc.get("security_opt") == ["no-new-privileges:true"], f"{name} must have no-new-privileges:true"


def test_n88_backup_metric_preservation_separation():
    """
    N-88: PostgreSQL backup script must separate completion metric from status metric.
    - backup_postgres_last_success.prom must ONLY be written on success so that subsequent
      failures do NOT clobber backup_last_completion_timestamp_seconds.
    - backup_postgres_status.prom must record backup_last_status (0 on failure, 1 on success).
    """
    backup_sh = Path("deployments/backup-postgres.sh").read_text(encoding="utf-8")

    # In cleanup / failure handler
    assert "backup_postgres_status.prom" in backup_sh
    assert "backup_last_status 0" in backup_sh
    # Failure handler must NOT overwrite or remove backup_postgres_last_success.prom
    cleanup_func = backup_sh[backup_sh.find("cleanup()"):backup_sh.find("trap cleanup EXIT")]
    assert "backup_postgres_last_success.prom" not in cleanup_func

    # In success block
    success_block = backup_sh[backup_sh.find("Publish completion metrics"):backup_sh.find("if [[ -n \"${BACKUP_REMOTE_DEST:-}\" ]]")]
    assert "backup_postgres_last_success.prom" in success_block
    assert "backup_last_completion_timestamp_seconds" in success_block
    assert "backup_last_status 1" in success_block
