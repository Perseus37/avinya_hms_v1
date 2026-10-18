from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
from starlette.requests import Request

from backend.control_plane.app import app
from backend.core.dependencies import RateLimiter
from backend.core.database import tenant_engine_pool


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

    def request_for(host):
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/auth/login",
                "headers": [(b"host", host.encode())],
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
