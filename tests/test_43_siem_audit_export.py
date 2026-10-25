"""Tests for Security Audit Log Export and SIEM Streaming Integration (M-14)."""

import json
from datetime import datetime, timezone
import pytest


def test_query_security_events_default(client, superuser_headers):
    """Verifies that security events across ledgers can be retrieved with pagination."""
    res = client.get(
        "/v1/api/audit/security-events",
        headers=superuser_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert "total_events" in data
    assert "events" in data
    assert isinstance(data["events"], list)


def test_query_security_events_filtered(client, superuser_headers):
    """Verifies filtering by category and severity."""
    res = client.get(
        "/v1/api/audit/security-events?category=PRIVACY_CONSENT&limit=10",
        headers=superuser_headers,
    )
    assert res.status_code == 200
    data = res.json()
    for ev in data["events"]:
        assert ev["category"] == "PRIVACY_CONSENT"


def test_stream_security_events_ndjson(client, superuser_headers):
    """Verifies that the /stream endpoint returns valid newline-delimited JSON (NDJSON)."""
    res = client.get(
        "/v1/api/audit/stream?limit=100",
        headers=superuser_headers,
    )
    assert res.status_code == 200
    assert "application/x-ndjson" in res.headers["content-type"]

    lines = [line.strip() for line in res.text.split("\n") if line.strip()]
    for line in lines:
        parsed = json.loads(line)
        assert "event_id" in parsed
        assert "timestamp" in parsed
        assert "category" in parsed
        assert "severity" in parsed
        assert "action" in parsed


def test_siem_webhook_ssrf_protection_blocks_loopback(client, superuser_headers):
    """Verifies that loopback and internal addresses are blocked with 400 Bad Request."""
    res = client.post(
        "/v1/api/audit/siem/webhook-test",
        headers=superuser_headers,
        json={"webhook_url": "http://127.0.0.1:19999/siem/invalid"},
    )
    assert res.status_code == 400
    assert "SSRF Protection" in res.json()["message"]


def test_security_events_with_breach_incident(client, superuser_headers):
    """Verifies SIEM export queries successfully when a PrivacyDataBreachIncident exists."""
    from backend.core.database import tenant_engine_pool
    from sqlalchemy.orm import Session
    from backend.domains.privacy.models import PrivacyDataBreachIncident

    engine = tenant_engine_pool.get_engine("hms_default")
    with Session(bind=engine) as session:
        breach = PrivacyDataBreachIncident(
            incident_code="BR-TEST-001",
            title="Test Phishing Incident",
            description="Suspicious email attachment flagged by EDR",
            severity="HIGH",
            status="REPORTED",
            breach_type="CONFIDENTIALITY",
            affected_data_categories=["PATIENT_DEMOGRAPHICS"],
            estimated_affected_subjects=5,
            reported_by_user_id=1,
        )
        session.add(breach)
        session.commit()

    res = client.get(
        "/v1/api/audit/security-events?category=DATA_BREACH",
        headers=superuser_headers,
    )
    assert res.status_code == 200
    events = res.json()["events"]
    assert len(events) >= 1
    assert any(ev["details"]["incident_code"] == "BR-TEST-001" for ev in events)


def test_unauthorized_user_blocked_from_security_events(client, nurse_headers):
    """Users without privacy:read or audit permission are blocked with 403 Forbidden."""
    res = client.get(
        "/v1/api/audit/security-events",
        headers=nurse_headers,
    )
    assert res.status_code == 403

