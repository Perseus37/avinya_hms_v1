"""Tenant-scoped policy lookup, decision evaluation, and audit persistence."""

import logging
from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import insert, select
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

# Keeps each IN (...) list well below database bind-parameter limits.
_CONSENT_QUERY_CHUNK = 500
_UNSET = object()


def commit_without_expiring(db: Session) -> None:
    """Commit privacy audit rows without expiring objects the route will serialize.

    A plain commit expires every loaded ORM object, so building the response
    afterwards would refresh each row with its own query.
    """
    previous = db.expire_on_commit
    db.expire_on_commit = False
    try:
        db.commit()
    finally:
        db.expire_on_commit = previous


def _active_consent_revision(db: Session) -> PrivacyPolicyRevision | None:
    return db.execute(
        select(PrivacyPolicyRevision).where(
            PrivacyPolicyRevision.is_active.is_(True),
            PrivacyPolicyRevision.policy_type == "CONSENT",
        )
    ).scalar_one_or_none()


def evaluate_tenant_data_use_for_patients(
    db: Session,
    *,
    data_use_point: str,
    patient_ages: dict[int, int | None],
    actor_user_id: int | None,
    scope_key: str | None = None,
    commit_audit: bool = True,
    revision: PrivacyPolicyRevision | None | object = _UNSET,
) -> dict[int, ConsentDecision]:
    """Evaluate one data-use point for many patients with a constant query count.

    The active policy is looked up once, all consent evidence is loaded with one
    query per chunk of patients, and audit events are persisted in one bulk add.
    Decisions and audit rows are identical to evaluating each patient separately.
    """
    if data_use_point not in DATA_USE_POINTS:
        raise ValueError(f"Unregistered privacy data-use point: {data_use_point}")
    if not patient_ages:
        return {}

    if revision is _UNSET:
        revision = _active_consent_revision(db)
    if revision is None:
        off = evaluate_data_use(None, data_use_point=data_use_point, mode="OFF")
        return {patient_id: off for patient_id in patient_ages}

    try:
        policy = ConsentPolicyV1.model_validate(revision.policy_document)
    except ValidationError:
        logger.exception(
            "Active privacy policy failed runtime validation (revision_id=%s)",
            revision.id,
        )
        invalid = ConsentDecision(
            True, "active_policy_invalid_audit_only", None, "AUDIT_ONLY", True
        )
        decisions = {patient_id: invalid for patient_id in patient_ages}
        _record_audits(
            db,
            revision_id=revision.id,
            actor_user_id=actor_user_id,
            data_use_point=data_use_point,
            results=decisions.values(),
            commit=commit_audit,
        )
        return decisions

    purpose_code = policy.data_use_points.get(data_use_point)
    purpose = next(
        (item for item in policy.purposes if item.code == purpose_code), None
    )

    rows_by_patient: dict[int, list[PatientConsentRecord]] = {}
    if purpose_code is not None:
        patient_ids = list(patient_ages)
        for start in range(0, len(patient_ids), _CONSENT_QUERY_CHUNK):
            chunk = patient_ids[start : start + _CONSENT_QUERY_CHUNK]
            rows = db.execute(
                select(PatientConsentRecord)
                .where(
                    PatientConsentRecord.patient_id.in_(chunk),
                    PatientConsentRecord.purpose == purpose_code,
                    PatientConsentRecord.policy_revision_id == revision.id,
                )
                .order_by(
                    PatientConsentRecord.recorded_at.desc(),
                    PatientConsentRecord.id.desc(),
                )
            ).scalars().all()
            for row in rows:
                rows_by_patient.setdefault(row.patient_id, []).append(row)

    decisions: dict[int, ConsentDecision] = {}
    for patient_id, patient_age in patient_ages.items():
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
            for row in rows_by_patient.get(patient_id, [])
            if purpose is not None
            and row.scope_type == purpose.scope
            and (purpose.scope == "patient" or row.scope_key == scope_key)
        ]
        decisions[patient_id] = evaluate_data_use(
            policy,
            data_use_point=data_use_point,
            mode=revision.enforcement_mode,
            consents=consents,
            policy_revision_id=revision.id,
            recipient_or_scope_key=scope_key,
            patient_age=patient_age,
        )
    _record_audits(
        db,
        revision_id=revision.id,
        actor_user_id=actor_user_id,
        data_use_point=data_use_point,
        results=decisions.values(),
        commit=commit_audit,
    )
    return decisions


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
    return evaluate_tenant_data_use_for_patients(
        db,
        data_use_point=data_use_point,
        patient_ages={patient_id: patient_age},
        actor_user_id=actor_user_id,
        scope_key=scope_key,
        commit_audit=commit_audit,
    )[patient_id]


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

    revision = _active_consent_revision(db)
    if revision is None:
        # No active policy: nothing to evaluate or audit, so skip loading patients.
        if data_use_point not in DATA_USE_POINTS:
            raise ValueError(f"Unregistered privacy data-use point: {data_use_point}")
        off = evaluate_data_use(None, data_use_point=data_use_point, mode="OFF")
        return [off for _ in distinct_patient_ids]

    patients = db.execute(
        select(Patient).where(Patient.id.in_(distinct_patient_ids))
    ).scalars()
    ages = {
        patient.id: calculate_patient_age(patient.date_of_birth)
        for patient in patients
    }
    decisions = evaluate_tenant_data_use_for_patients(
        db,
        data_use_point=data_use_point,
        patient_ages={patient_id: ages.get(patient_id) for patient_id in distinct_patient_ids},
        actor_user_id=actor_user_id,
        commit_audit=False,
        revision=revision,
    )
    commit_without_expiring(db)
    return [decisions[patient_id] for patient_id in distinct_patient_ids]


def _record_audits(
    db: Session,
    *,
    revision_id: int | None,
    actor_user_id: int | None,
    data_use_point: str,
    results: Iterable[ConsentDecision],
    commit: bool,
) -> None:
    occurred_at = datetime.now(timezone.utc)
    rows = [
        {
            "policy_revision_id": revision_id,
            "actor_user_id": actor_user_id,
            "data_use_point": data_use_point,
            "purpose_code": result.purpose_code,
            "mode": result.mode,
            "result": "WOULD_DENY" if result.would_deny else (
                "ALLOW" if result.allowed else "DENY"
            ),
            "reason_code": result.reason_code,
            "occurred_at": occurred_at,
        }
        for result in results
    ]
    if rows:
        # One executemany call regardless of how many patients were evaluated.
        db.execute(insert(PrivacyDecisionAudit), rows)
    if commit:
        commit_without_expiring(db)
    else:
        db.flush()
