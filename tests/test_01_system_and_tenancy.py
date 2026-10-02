from sqlalchemy.orm import sessionmaker
from models import Floor


def test_01_liveness_probe(client):
    """Scenario 1: GET /health returns 200 with healthy status."""
    live_res = client.get("/health")
    assert live_res.status_code == 200
    assert live_res.json()["status"] == "healthy"
    assert live_res.json()["success"] is True


def test_02_deep_readiness_probe(client):
    """Scenario 2: GET /health/ready verifies both PostgreSQL/SQLite and Redis connectivity."""
    ready_res = client.get("/health/ready", headers={"X-Hospital-Subdomain": "default"})
    assert ready_res.status_code == 200
    body = ready_res.json()
    assert body["status"] == "ready"
    assert body["database"] == "connected"
    assert body["redis_cache"] == "connected"


def test_03_subdomain_extraction_via_header_and_host(client, apollo_admin_headers):
    """Scenario 3: Subdomain resolves from X-Hospital-Subdomain or Host header."""
    res_header = client.get("/v1/api/auth/me", headers=apollo_admin_headers)
    assert res_header.status_code == 200
    assert res_header.json()["email"] == "admin@apollo.com"

    host_headers = {
        "Authorization": apollo_admin_headers["Authorization"],
        "Host": "apollo.localhost",
    }
    res_host = client.get("/v1/api/auth/me", headers=host_headers)
    assert res_host.status_code == 200
    assert res_host.json()["email"] == "admin@apollo.com"


def test_04_unregistered_hospital_subdomain_returns_404(client):
    """Scenario 4: Unregistered hospital subdomain is rejected with 404."""
    res = client.get("/health/ready", headers={"X-Hospital-Subdomain": "nonexistent-hospital"})
    assert res.status_code == 404
    assert "not registered" in res.json()["message"].lower()


def test_05_suspended_hospital_subdomain_returns_403(client):
    """Scenario 5: Suspended hospital (is_active=False) is rejected with 403."""
    res = client.get("/health/ready", headers={"X-Hospital-Subdomain": "suspended"})
    assert res.status_code == 403
    assert "suspended" in res.json()["message"].lower()


def test_06_onprem_hospital_routed_to_cloud_returns_400(client):
    """Scenario 6: ON_PREM hospital routed to Cloud API is rejected with 400."""
    res = client.get("/health/ready", headers={"X-Hospital-Subdomain": "onprem"})
    assert res.status_code == 400
    assert "on-premise" in res.json()["message"].lower()


def test_07_request_id_correlation_and_sanitization(client):
    """Scenario 7: Valid X-Request-ID is echoed; invalid/malicious X-Request-ID is replaced."""
    valid_id = "REQ-2026-SAFE_ID-001"
    res_valid = client.get("/health", headers={"X-Request-ID": valid_id})
    assert res_valid.headers["X-Request-ID"] == valid_id
    assert "X-Process-Time-Ms" in res_valid.headers

    malicious_id = "bad id with spaces and \n newline injection!"
    res_sanitized = client.get("/health", headers={"X-Request-ID": malicious_id})
    assert res_sanitized.headers["X-Request-ID"] != malicious_id
    assert len(res_sanitized.headers["X-Request-ID"]) == 36


def test_08_automatic_audit_actor_stamping(client, multi_tenant_env, superuser_headers):
    """Scenario 8: stamp_audit_actor automatically records created_by_id and updated_by_id."""
    create_res = client.post(
        "/v1/api/ip/floors",
        headers=superuser_headers,
        json={"floor_number": 10, "name": "Tenth Floor"},
    )
    assert create_res.status_code == 201
    floor_id = create_res.json()["floor_id"]

    DefaultSession = sessionmaker(bind=multi_tenant_env["tenant_engines"]["hms_default"])
    with DefaultSession() as db:
        floor = db.get(Floor, floor_id)
        assert floor is not None
        assert floor.created_by_id == 1
        assert floor.updated_by_id == 1


def test_09_pagination_boundary_validation(client, superuser_headers):
    """Scenario 9: page < 1 or page_size > 200 returns 400 Bad Request."""
    res_bad_page = client.get("/v1/api/auth/users?page=0&page_size=20", headers=superuser_headers)
    assert res_bad_page.status_code == 400

    res_bad_size = client.get("/v1/api/auth/users?page=1&page_size=500", headers=superuser_headers)
    assert res_bad_size.status_code == 400

def test_10_rate_limiter_blocks_excessive_login_attempts(client):
    """Scenario 10: Exceeding 10 login requests per minute from the same IP returns 429 Too Many Requests."""
    for _ in range(10):
        res = client.post(
            "/v1/api/auth/login",
            headers={"X-Hospital-Subdomain": "default"},
            json={"email": "nonexistent@default.com", "password": " password123"},
        )
        assert res.status_code == 401

    # 11th attempt within the 60s window is blocked by RateLimiter with 429
    blocked_res = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "nonexistent@default.com", "password": "password123"},
    )
    assert blocked_res.status_code == 429
    assert "rate limit exceeded" in blocked_res.json()["message"].lower()