"""Pure consent decision evaluation and counts-only decision audit reporting."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from backend.domains.privacy.policy_schema import ConsentPolicyV1
from backend.domains.privacy.data_use_points import DATA_USE_POINTS


@dataclass(frozen=True)
class ConsentEvidence:
    id: int
    decision: str
    source: str
    evidence_reference: str | None
    expires_at: datetime | None
    recorded_at: datetime
    consenter_type: str
    scope_type: str
    scope_key: str | None
    policy_revision_id: int | None


@dataclass(frozen=True)
class ConsentDecision:
    allowed: bool
    reason_code: str
    purpose_code: str | None
    mode: str
    would_deny: bool = False


SOURCE_TO_CAPTURE = {
    "WRITTEN": "WRITTEN",
    "E_SIGNATURE": "E_SIGNATURE",
    "OTP": "OTP",
    "DIGITAL_OPT_IN": "DIGITAL_OPT_IN",
    "DIGITAL": "DIGITAL_OPT_IN",
    "WITNESSED_VERBAL": "WITNESSED_VERBAL",
    "VERBAL": "WITNESSED_VERBAL",
    "GUARDIAN_SIGNATURE": "GUARDIAN_SIGNATURE",
    "GUARDIAN": "GUARDIAN_SIGNATURE",
    "ABDM_ARTIFACT": "ABDM_ARTIFACT",
}


def evaluate_data_use(
    policy: ConsentPolicyV1 | None,
    *,
    data_use_point: str,
    mode: str = "OFF",
    consents: Iterable[ConsentEvidence] = (),
    policy_revision_id: int | None = None,
    recipient_or_scope_key: str | None = None,
    exception_code: str | None = None,
    actor_permissions: set[str] | None = None,
    exception_reason: str | None = None,
    now: datetime | None = None,
    patient_age: int | None = None,
) -> ConsentDecision:
    """Evaluate a single data-use request; audit-only never denies the caller."""
    if mode == "OFF" or policy is None:
        return ConsentDecision(True, "policy_off", None, "OFF")
    if mode not in {"AUDIT_ONLY", "ENFORCE"}:
        return ConsentDecision(True, "unsupported_mode", None, mode, True)

    purpose_code = policy.data_use_points.get(data_use_point)
    if purpose_code is None:
        return _result(True, "unmapped_data_use_point", None, mode)

    purpose = next(
        (item for item in policy.purposes if item.code == purpose_code), None
    )
    if purpose is None:
        return _result(False, "invalid_purpose_mapping", purpose_code, mode)
    if not purpose.consent_required:
        return _result(True, "not_consent_gated", purpose_code, mode)
    point = DATA_USE_POINTS.get(data_use_point)
    if point is not None and point.action not in purpose.required_before:
        return _result(True, "consent_not_required_at_this_action", purpose_code, mode)

    if purpose_code in policy.never_override and exception_code is not None:
        return _result(False, "purpose_never_overridden", purpose_code, mode)

    if exception_code is not None:
        exception = next(
            (item for item in policy.exceptions if item.code == exception_code),
            None,
        )
        if exception is not None and purpose_code in exception.covers:
            permissions = actor_permissions or set()
            if (
                exception.invoker_permission in permissions
                and (not exception.reason_required or (exception_reason or "").strip())
            ):
                return _result(True, f"exception:{exception.code}", purpose_code, mode)
            return _result(False, "exception_not_authorized_or_reason_missing", purpose_code, mode)

    now = now or datetime.now(timezone.utc)
    required_group_consenters: set[str] = set()
    for group in policy.groups:
        if group.status != "configured" or group.condition is None:
            continue
        if "age_lt" in group.condition:
            threshold = int(group.condition["age_lt"])
            if patient_age is None or patient_age < threshold:
                required_group_consenters.add(group.consenter)

    matching_records = []
    for record in consents:
        if record.policy_revision_id != policy_revision_id:
            continue
        if record.scope_type != purpose.scope:
            continue
        if purpose.scope != "patient" and record.scope_key != recipient_or_scope_key:
            continue
        matching_records.append(record)

    latest = max(
        matching_records,
        key=lambda record: (record.recorded_at, record.id),
        default=None,
    )
    if latest is not None and latest.decision != "GRANTED":
        reason = (
            "consent_withdrawn"
            if latest.decision == "WITHDRAWN"
            else "consent_declined"
        )
        return _result(False, reason, purpose_code, mode)
    if latest is not None:
        expiry = latest.expires_at
        if expiry is not None:
            expiry = (
                expiry.replace(tzinfo=timezone.utc)
                if expiry.tzinfo is None
                else expiry
            )
        if expiry is None or expiry > now:
            if (
                (purpose.validity != "fixed_term" or expiry is not None)
                and SOURCE_TO_CAPTURE.get(latest.source) in purpose.capture
                and bool(latest.evidence_reference)
                and latest.consenter_type in purpose.consenters
                and required_group_consenters.issubset({latest.consenter_type})
            ):
                return _result(True, "valid_consent", purpose_code, mode)
            return _result(False, "consent_record_invalid", purpose_code, mode)

    if purpose.default_when_no_record == "allow":
        return _result(True, "no_record_default_allow", purpose_code, mode)
    if purpose.default_when_no_record == "ask":
        return _result(False, "consent_required", purpose_code, mode)
    return _result(False, "consent_missing_or_invalid", purpose_code, mode)


def _result(
    allowed: bool, reason: str, purpose_code: str | None, mode: str
) -> ConsentDecision:
    if mode == "AUDIT_ONLY":
        return ConsentDecision(
            True,
            reason,
            purpose_code,
            mode,
            would_deny=not allowed,
        )
    return ConsentDecision(allowed, reason, purpose_code, mode)


def counts_only_report(
    rows: Iterable[tuple[str, str, str] | tuple[str, str, str, int]],
) -> list[dict[str, str | int]]:
    """Aggregate (data-use point, result, reason) rows without returning identifiers."""
    counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        point, result, reason = row[:3]
        row_count = row[3] if len(row) == 4 else 1
        key = (point, result, reason)
        counts[key] = counts.get(key, 0) + row_count
    return [
        {
            "data_use_point": point,
            "result": result,
            "reason_code": reason,
            "count": count,
        }
        for (point, result, reason), count in sorted(counts.items())
    ]
