"""Tenant-scoped policy lookup, decision evaluation, and audit persistence."""

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from backend.domains.privacy.data_use_points import DATA_USE_POINTS
from backend.domains.privacy.decision_engine import (
    ConsentEvidence,
    ConsentDecision,
    evaluate_data_use,
)
from backend.domains.privacy.models import (
    PatientConsentRecord,
    PrivacyDecisionAudit,
    PrivacyPolicyRevision,
)
from backend.domains.patients.models import Patient
from backend.domains.privacy.policy_schema import ConsentPolicyV1

logger = logging.getLogger(__name__)


def evaluate_tenant_data_use(
    db: Session,
    *,
    data_use_point: str,
    patient_id: int,
    actor_user_id: int | None,
    scope_key: str | None = None,
    patient_age: int | None = None,
    commit_audit: bool = True,
) -> ConsentDecision:
    """Evaluate an active tenant policy and persist a PHI-minimized audit event."""
    if data_use_point not in DATA_USE_POINTS:
        raise ValueError(f"Unregistered privacy data-use point: {data_use_point}")

    revision = db.execute(
        select(PrivacyPolicyRevision).where(
            PrivacyPolicyRevision.is_active.is_(True),
            PrivacyPolicyRevision.policy_type == "CONSENT",
        )
    ).scalar_one_or_none()
    if revision is None:
        return evaluate_data_use(
            None, data_use_point=data_use_point, mode="OFF"
        )

    try:
        policy = ConsentPolicyV1.model_validate(revision.policy_document)
    except ValidationError:
        logger.exception(
            "Active privacy policy failed runtime validation (revision_id=%s)",
            revision.id,
        )
        result = ConsentDecision(
            True, "active_policy_invalid_audit_only", None, "AUDIT_ONLY", True
        )
        _record_audit(
            db,
            revision_id=revision.id,
            actor_user_id=actor_user_id,
            data_use_point=data_use_point,
            result=result,
            commit=commit_audit,
        )
        return result

    purpose_code = policy.data_use_points.get(data_use_point)
    consent_rows = []
    if purpose_code is not None:
        consent_rows = db.execute(
            select(PatientConsentRecord)
            .where(
                PatientConsentRecord.patient_id == patient_id,
                PatientConsentRecord.purpose == purpose_code,
                PatientConsentRecord.policy_revision_id == revision.id,
            )
            .order_by(
                PatientConsentRecord.recorded_at.desc(),
                PatientConsentRecord.id.desc(),
            )
        ).scalars().all()

    purpose = next(
        (item for item in policy.purposes if item.code == purpose_code), None
    )
    consents = [
        ConsentEvidence(
            id=row.id,
            decision=row.decision,
            source=row.source,
            evidence_reference=row.evidence_reference,
            expires_at=row.expires_at,
            recorded_at=row.recorded_at,
            scope_type=row.scope_type,
            scope_key=row.scope_key,
            policy_revision_id=row.policy_revision_id,
            consenter_type=row.consenter_type,
        )
        for row in consent_rows
        if purpose is not None
        and row.scope_type == purpose.scope
        and (
            purpose.scope == "patient"
            or row.scope_key == scope_key
        )
    ]
    result = evaluate_data_use(
        policy,
        data_use_point=data_use_point,
        mode=revision.enforcement_mode,
        consents=consents,
        policy_revision_id=revision.id,
        recipient_or_scope_key=scope_key,
        patient_age=patient_age,
    )
    _record_audit(
        db,
        revision_id=revision.id,
        actor_user_id=actor_user_id,
        data_use_point=data_use_point,
        result=result,
        commit=commit_audit,
    )
    return result


def calculate_patient_age(date_of_birth: datetime | date) -> int:
    birth_date = (
        date_of_birth.date() if isinstance(date_of_birth, datetime) else date_of_birth
    )
    today = datetime.now(timezone.utc).date()
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


def evaluate_tenant_patient_data_uses(
    db: Session,
    *,
    data_use_point: str,
    patient_ids: list[int],
    actor_user_id: int | None,
) -> list[ConsentDecision]:
    """Evaluate and audit one point for each distinct patient in a result set."""
    distinct_patient_ids = sorted(set(patient_ids))
    if not distinct_patient_ids:
        return []

    patients = db.execute(
        select(Patient).where(Patient.id.in_(distinct_patient_ids))
    ).scalars()
    ages = {
        patient.id: calculate_patient_age(patient.date_of_birth)
        for patient in patients
    }
    decisions = [
        evaluate_tenant_data_use(
            db,
            data_use_point=data_use_point,
            patient_id=patient_id,
            actor_user_id=actor_user_id,
            patient_age=ages.get(patient_id),
            commit_audit=False,
        )
        for patient_id in distinct_patient_ids
    ]
    db.commit()
    return decisions


def _record_audit(
    db: Session,
    *,
    revision_id: int | None,
    actor_user_id: int | None,
    data_use_point: str,
    result: ConsentDecision,
    commit: bool,
) -> None:
    db.add(
        PrivacyDecisionAudit(
            policy_revision_id=revision_id,
            actor_user_id=actor_user_id,
            data_use_point=data_use_point,
            purpose_code=result.purpose_code,
            mode=result.mode,
            result="WOULD_DENY" if result.would_deny else (
                "ALLOW" if result.allowed else "DENY"
            ),
            reason_code=result.reason_code,
            occurred_at=datetime.now(timezone.utc),
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
