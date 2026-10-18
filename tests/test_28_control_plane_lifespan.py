from fastapi.testclient import TestClient
from fastapi import HTTPException
from starlette.requests import Request
import pytest
from starlette.requests import Request

from backend.control_plane.app import app
from backend.core.dependencies import RateLimiter, RequireLicensedModule
from backend.core.database import tenant_engine_pool
from backend.core.config import settings
from backend.control_plane.services.auth import verify_control_plane_secret


def test_control_plane_lifespan_disposes_tenant_engines(monkeypatch):
    disposed = []
    monkeypatch.setattr(
        tenant_engine_pool, "dispose_all", lambda: disposed.append(True)
    )

    with TestClient(app):
        assert disposed == []

    assert disposed == [True]


def test_login_rate_limit_is_scoped_per_tenant_host():
    class MemoryRedis:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, ex=None):
            self.values[key] = value

    def request_for(host, forwarded_for=None):
        headers = [(b"host", host.encode())]
        if forwarded_for:
            headers.append((b"x-forwarded-for", forwarded_for.encode()))
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/auth/login",
                "headers": headers,
                "query_string": b"",
                "server": (host, 443),
                "client": ("192.0.2.10", 1234),
                "scheme": "https",
            }
        )

    redis = MemoryRedis()
    limiter = RateLimiter(scope="auth_login", max_requests=1, window_seconds=60)
    limiter(request_for("hospital-a.localhost"), redis)
    with pytest.raises(HTTPException) as exc_info:
        limiter(request_for("hospital-a.localhost"), redis)
    assert exc_info.value.status_code == 429
    limiter(request_for("hospital-b.localhost"), redis)
    limiter(request_for("untrusted.example.test"), redis)
    with pytest.raises(HTTPException) as default_tenant_limit:
        limiter(request_for("localhost"), redis)

    assert default_tenant_limit.value.status_code == 429
    assert len(redis.values) == 3

    spoofed_headers = RateLimiter(
        scope="auth_login", max_requests=1, window_seconds=60
    )
    spoofed_headers(request_for("hospital-c.localhost", "198.51.100.1"), redis)
    with pytest.raises(HTTPException) as forwarded_for_bypass:
        spoofed_headers(
            request_for("hospital-c.localhost", "203.0.113.2"), redis
        )
    assert forwarded_for_bypass.value.status_code == 429


def test_control_plane_secret_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "CONTROL_PLANE_MASTER_KEY", None)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    with pytest.raises(HTTPException) as exc_info:
        verify_control_plane_secret(request)
    assert exc_info.value.status_code == 503


def test_module_license_fails_closed_for_missing_or_invalid_configuration(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "backend.core.dependencies.resolve_tenant_context",
        lambda _request: SimpleNamespace(enabled_modules_json="{}"),
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/test",
            "headers": [],
            "query_string": b"",
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    )
    with pytest.raises(HTTPException) as missing_module:
        RequireLicensedModule("opd")(request)
    assert missing_module.value.status_code == 403

    monkeypatch.setattr(
        "backend.core.dependencies.resolve_tenant_context",
        lambda _request: SimpleNamespace(enabled_modules_json="{"),
    )
    with pytest.raises(HTTPException) as invalid_configuration:
        RequireLicensedModule("opd")(request)
    assert invalid_configuration.value.status_code == 503


def test_invalid_trusted_proxy_configuration_blocks_auth_requests(monkeypatch):
    class MemoryRedis:
        def get(self, _key):
            return None

        def set(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "not-an-ip-range")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [],
            "query_string": b"",
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        RateLimiter(scope="auth_login", max_requests=1)(request, MemoryRedis())
    assert exc_info.value.status_code == 503
