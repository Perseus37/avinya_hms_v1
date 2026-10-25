"""Security audit log and SIEM export router for Avinya HMS.

Provides structured, queryable access and NDJSON streaming for security events,
access decisions, emergency overrides, data breaches, and retention operations.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, AsyncGenerator, Generator, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.patients.models import BreakGlassGrant
from backend.domains.privacy.models import (
    PatientDataRightsRequest,
    PrivacyDataBreachIncident,
    PrivacyDecisionAudit,
    PrivacyRetentionRunLedger,
)

router = APIRouter(prefix="/api/audit", tags=["Security & SIEM Audit Export"])


class SiemEvent(BaseModel):
    event_id: str
    timestamp: datetime
    category: str
    severity: str
    action: str
    actor_user_id: Optional[int] = None
    target_patient_id: Optional[int] = None
    details: dict[str, Any]


class SiemExportResponse(BaseModel):
    total_events: int
    limit: int
    offset: int
    events: List[SiemEvent]


class SiemWebhookTestRequest(BaseModel):
    webhook_url: str
    auth_header: Optional[str] = None


def _collect_security_events(
    db: Session,
    *,
    from_time: Optional[datetime] = None,
    to_time: Optional[datetime] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[SiemEvent]]:
    """Gathers and normalizes events across multiple security & governance ledgers."""
    events: list[SiemEvent] = []

    # 1. Privacy Decision Audits
    if not category or category.upper() == "PRIVACY_CONSENT":
        q = select(PrivacyDecisionAudit)
        if from_time:
            q = q.where(PrivacyDecisionAudit.occurred_at >= from_time)
        if to_time:
            q = q.where(PrivacyDecisionAudit.occurred_at <= to_time)
        p_audits = db.execute(q.order_by(desc(PrivacyDecisionAudit.id)).limit(limit)).scalars().all()

        for pa in p_audits:
            sev = "INFO"
            if pa.result == "WOULD_DENY":
                sev = "MEDIUM"
            elif pa.result == "DENY":
                sev = "HIGH"
            elif pa.result in ("BYPASS_ALLOWED", "EMERGENCY_OVERRIDE"):
                sev = "CRITICAL"

            if not severity or severity.upper() == sev:
                events.append(
                    SiemEvent(
                        event_id=f"AUDIT-PRIV-{pa.id}",
                        timestamp=pa.occurred_at if pa.occurred_at.tzinfo else pa.occurred_at.replace(tzinfo=timezone.utc),
                        category="PRIVACY_CONSENT",
                        severity=sev,
                        action=f"DATA_USE_{pa.result}",
                        actor_user_id=pa.actor_user_id,
                        details={
                            "data_use_point": pa.data_use_point,
                            "mode": pa.mode,
                            "reason_code": pa.reason_code,
                            "purpose_code": pa.purpose_code,
                            "policy_revision_id": pa.policy_revision_id,
                        },
                    )
                )

    # 2. Break-Glass Emergency Overrides
    if not category or category.upper() == "BREAK_GLASS":
        q_bg = select(BreakGlassGrant)
        if from_time:
            q_bg = q_bg.where(BreakGlassGrant.created_at >= from_time)
        if to_time:
            q_bg = q_bg.where(BreakGlassGrant.created_at <= to_time)
        bg_records = db.execute(q_bg.order_by(desc(BreakGlassGrant.id)).limit(limit)).scalars().all()

        for bg in bg_records:
            sev = "CRITICAL"
            if not severity or severity.upper() == sev:
                events.append(
                    SiemEvent(
                        event_id=f"AUDIT-BG-{bg.id}",
                        timestamp=bg.created_at if bg.created_at.tzinfo else bg.created_at.replace(tzinfo=timezone.utc),
                        category="BREAK_GLASS",
                        severity=sev,
                        action="EMERGENCY_OVERRIDE_ACTIVATED",
                        actor_user_id=bg.user_id,
                        target_patient_id=bg.patient_id,
                        details={
                            "reason": bg.reason,
                            "expires_at": bg.expires_at.isoformat() if bg.expires_at else None,
                            "review_outcome": bg.review_outcome,
                        },
                    )
                )

    # 3. Personal Data Breach Incidents
    if not category or category.upper() == "DATA_BREACH":
        q_br = select(PrivacyDataBreachIncident)
        if from_time:
            q_br = q_br.where(PrivacyDataBreachIncident.created_at >= from_time)
        if to_time:
            q_br = q_br.where(PrivacyDataBreachIncident.created_at <= to_time)
        breaches = db.execute(q_br.order_by(desc(PrivacyDataBreachIncident.id)).limit(limit)).scalars().all()

        for br in breaches:
            sev = br.severity.value if hasattr(br.severity, "value") else str(br.severity)
            if not severity or severity.upper() == sev.upper():
                events.append(
                    SiemEvent(
                        event_id=f"AUDIT-BREACH-{br.id}",
                        timestamp=br.created_at if br.created_at.tzinfo else br.created_at.replace(tzinfo=timezone.utc),
                        category="DATA_BREACH",
                        severity=sev.upper(),
                        action=f"BREACH_{br.status.value if hasattr(br.status, 'value') else str(br.status)}",
                        actor_user_id=br.reported_by_user_id,
                        details={
                            "incident_code": br.incident_code,
                            "title": br.title,
                            "affected_records_count": br.estimated_affected_subjects,
                            "certin_reported": br.reported_to_authority_at is not None,
                        },
                    )
                )

    # 4. Retention Purges
    if not category or category.upper() == "RETENTION_PURGE":
        q_ret = select(PrivacyRetentionRunLedger)
        if from_time:
            q_ret = q_ret.where(PrivacyRetentionRunLedger.executed_at >= from_time)
        if to_time:
            q_ret = q_ret.where(PrivacyRetentionRunLedger.executed_at <= to_time)
        runs = db.execute(q_ret.order_by(desc(PrivacyRetentionRunLedger.id)).limit(limit)).scalars().all()

        for ret in runs:
            sev = "INFO" if ret.dry_run else "MEDIUM"
            if not severity or severity.upper() == sev:
                events.append(
                    SiemEvent(
                        event_id=f"AUDIT-RET-{ret.id}",
                        timestamp=ret.executed_at if ret.executed_at.tzinfo else ret.executed_at.replace(tzinfo=timezone.utc),
                        category="RETENTION_PURGE",
                        severity=sev,
                        action="RETENTION_DRY_RUN" if ret.dry_run else "RETENTION_EXECUTED",
                        actor_user_id=ret.executed_by_user_id,
                        details={
                            "action": ret.action,
                            "dry_run": ret.dry_run,
                            "categories": ret.categories,
                            "affected_count": ret.affected_count,
                            "held_count": ret.held_count,
                            "approval_reference": ret.approval_reference,
                        },
                    )
                )

    # Sort all merged events by timestamp descending
    events.sort(key=lambda e: e.timestamp, reverse=True)
    total = len(events)
    paginated = events[offset : offset + limit]
    return total, paginated


@router.get(
    "/security-events",
    response_model=SiemExportResponse,
    summary="Query security audit events for SIEM/SOC review",
)
def get_security_events(
    from_time: Optional[datetime] = None,
    to_time: Optional[datetime] = None,
    category: Optional[str] = Query(None, description="Event category: PRIVACY_CONSENT, BREAK_GLASS, DATA_BREACH, RETENTION_PURGE"),
    severity: Optional[str] = Query(None, description="Severity: INFO, LOW, MEDIUM, HIGH, CRITICAL"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:read")),
):
    """Returns normalized security audit logs formatted for SOC/SIEM inspection."""
    total, events = _collect_security_events(
        db,
        from_time=from_time,
        to_time=to_time,
        category=category,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    return SiemExportResponse(
        total_events=total,
        limit=limit,
        offset=offset,
        events=events,
    )


@router.get(
    "/stream",
    summary="Stream security events as NDJSON for SIEM collectors (Splunk, Elastic, Wazuh)",
)
def stream_security_events(
    from_time: Optional[datetime] = None,
    to_time: Optional[datetime] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = Query(1000, ge=1, le=10000),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:read")),
):
    """Streams newline-delimited JSON (NDJSON) security logs for automated SIEM log shippers."""
    _, events = _collect_security_events(
        db,
        from_time=from_time,
        to_time=to_time,
        category=category,
        severity=severity,
        limit=limit,
        offset=0,
    )

    def ndjson_generator() -> Generator[str, None, None]:
        for event in events:
            yield event.model_dump_json() + "\n"

    return StreamingResponse(
        ndjson_generator(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=security-audit-events.ndjson"},
    )


@router.post(
    "/siem/webhook-test",
    summary="Test connectivity to configured SIEM forwarder or webhook",
)
def test_siem_webhook_connectivity(
    payload: SiemWebhookTestRequest,
    actor: User = Depends(RequirePermission("system:admin")),
):
    """Validates external SIEM ingestion pipeline connectivity with SSRF guards."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    import httpx

    parsed = urlparse(payload.webhook_url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook URL scheme. Only http/https supported.",
        )
    if not parsed.hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook URL: missing hostname.",
        )

    # SSRF Guard: Disallow loopback, private, link-local, and cloud-metadata addresses
    hostname = parsed.hostname.strip().lower()
    if hostname in ("localhost", "127.0.0.1", "::1", "169.254.169.254", "metadata.google.internal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSRF Protection: Webhook destination resolves to a restricted loopback or internal address.",
        )

    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if (
                    ip_obj.is_private
                    or ip_obj.is_loopback
                    or ip_obj.is_link_local
                    or ip_obj.is_multicast
                    or ip_obj.is_reserved
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="SSRF Protection: Webhook destination resolves to a restricted internal or link-local address.",
                    )
            except ValueError:
                pass
    except socket.gaierror:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot resolve webhook host '{hostname}'.",
        )

    test_payload = {
        "event": "SIEM_CONNECTIVITY_PROBE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "Avinya HMS",
        "actor": actor.email,
        "status": "HEALTHY",
    }
    headers = {"Content-Type": "application/json"}
    if payload.auth_header:
        headers["Authorization"] = payload.auth_header

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(payload.webhook_url, json=test_payload, headers=headers)
            return {
                "success": resp.is_success,
                "status_code": resp.status_code,
                "message": "Probe delivered successfully." if resp.is_success else "External SIEM returned error status.",
            }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SIEM webhook probe failed: {exc}",
        ) from exc

