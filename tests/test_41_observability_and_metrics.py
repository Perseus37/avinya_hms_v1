"""Tests for Prometheus observability and telemetry exposition (M-13)."""

import pytest
from fastapi.testclient import TestClient

from backend.core.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    get_prometheus_metrics_bytes,
    normalize_metric_path,
)
from backend.main import app


@pytest.fixture
def test_client():
    return TestClient(app)


def test_normalize_metric_path_prevents_high_cardinality():
    # Integer IDs
    assert normalize_metric_path("/api/patients/123") == "/api/patients/{id}"
    assert normalize_metric_path("/api/patients/456/encounters/789") == "/api/patients/{id}/encounters/{id}"

    # UUIDs
    assert normalize_metric_path("/api/records/12345678-1234-5678-1234-567812345678") == "/api/records/{uuid}"

    # Strips query params
    assert normalize_metric_path("/api/patients?page=1&limit=50") == "/api/patients"

    # Hex hashes
    assert normalize_metric_path("/v1/api/auth/reset/abcdef0123456789abcdef0123456789") == "/v1/api/auth/reset/{hash}"


def test_metrics_endpoint_returns_prometheus_format(test_client):
    response = test_client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text

    # Verify standard metrics are declared in exposition output
    assert "avinya_http_requests_total" in body
    assert "avinya_http_request_duration_seconds" in body
    assert "avinya_app_info" in body
    assert "avinya_queue_jobs_total" in body


def test_http_request_increments_telemetry_counters(test_client):
    # Make requests to liveness probe
    response = test_client.get("/health")
    assert response.status_code == 200

    metrics_res = test_client.get("/metrics")
    assert metrics_res.status_code == 200
    body = metrics_res.text

    # Look for /health metric counter line
    assert 'avinya_http_requests_total{endpoint="/health",method="GET",status="200"}' in body


def test_metrics_endpoint_authentication_protection(test_client, monkeypatch):
    from backend.core.config import settings

    # 1. Configured token requires valid auth
    monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", "secure-prometheus-token-123")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    unauth_res = test_client.get("/metrics")
    assert unauth_res.status_code == 401

    wrong_res = test_client.get("/metrics", headers={"Authorization": "Bearer wrong-token"})
    assert wrong_res.status_code == 401

    bearer_res = test_client.get("/metrics", headers={"Authorization": "Bearer secure-prometheus-token-123"})
    assert bearer_res.status_code == 200

    header_res = test_client.get("/metrics", headers={"X-Metrics-Token": "secure-prometheus-token-123"})
    assert header_res.status_code == 200

    # 2. Production without token blocks unauthenticated scrape
    monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", None)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    prod_res = test_client.get("/metrics")
    assert prod_res.status_code == 401

