"""Retention execution service for Avinya HMS.

Executes approved retention policies (delete, anonymise, archive)
while strictly honoring active legal holds.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from backend.domains.identity.models import User
from backend.domains.patients.models import Patient
from backend.domains.privacy.models import (
    PrivacyDecisionAudit,
    PrivacyLegalHold,
    PrivacyPolicyRevision,
)
from backend.domains.privacy.policy_schema import (
    RetentionPolicyV1,
    validate_policy_document,
)


def get_active_legal_holds(
    db: Session, now: datetime | None = None
) -> list[PrivacyLegalHold]:
    """Return all currently active (unreleased and unexpired) legal holds."""
    now = now or datetime.now(timezone.utc)
    stmt = select(PrivacyLegalHold).where(
        PrivacyLegalHold.released_at.is_(None),
        or_(PrivacyLegalHold.expires_at.is_(None), PrivacyLegalHold.expires_at > now),
    )
    return db.execute(stmt).scalars().all()


def is_category_on_hold(
    holds: list[PrivacyLegalHold], category_code: str
) -> bool:
    """Return True if an entire category is subject to an active legal hold."""
    return any(
        hold.data_category == category_code and hold.patient_id is None
        for hold in holds
    )


def get_held_patient_ids(
    holds: list[PrivacyLegalHold], category_code: str | None = None
) -> set[int]:
    """Return set of patient IDs protected by active legal holds."""
    return {
        hold.patient_id
        for hold in holds
        if hold.patient_id is not None
        and (category_code is None or hold.data_category in (category_code, None))
    }


def execute_retention_category(
    db: Session,
    *,
    category_code: str,
    action: str,
    retention_days: int,
    clock_start: str = "creation",
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Execute a retention action (delete, anonymise, archive) for a single category.
    Returns summary statistics of affected and held records.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    holds = get_active_legal_holds(db, now)

    # 1. If entire category is on legal hold, skip completely
    if is_category_on_hold(holds, category_code):
        return {
            "category_code": category_code,
            "action": action,
            "cutoff": cutoff.isoformat(),
            "candidate_count": 0,
            "held_count": 1,
            "affected_count": 0,
            "status": "SKIPPED_ACTIVE_LEGAL_HOLD",
        }

    held_patient_ids = get_held_patient_ids(holds, category_code)
    affected_count = 0

    # Category Handler: Privacy Decision Audits (Audit log retention)
    if category_code in ("privacy-decision-audits", "privacy_audits"):
        if action == "delete":
            candidate_stmt = select(func.count(PrivacyDecisionAudit.id)).where(
                PrivacyDecisionAudit.occurred_at < cutoff,
                PrivacyDecisionAudit.result == "ALLOW",
            )
            candidate_count = db.execute(candidate_stmt).scalar_one() or 0

            if not dry_run and candidate_count > 0:
                del_stmt = delete(PrivacyDecisionAudit).where(
                    PrivacyDecisionAudit.occurred_at < cutoff,
                    PrivacyDecisionAudit.result == "ALLOW",
                )
                res = db.execute(del_stmt)
                affected_count = res.rowcount
            else:
                affected_count = candidate_count

            return {
                "category_code": category_code,
                "action": action,
                "cutoff": cutoff.isoformat(),
                "candidate_count": candidate_count,
                "held_count": 0,
                "affected_count": affected_count,
                "status": "SUCCESS",
            }

    # Category Handler: Inactive Patients (Patient Record Retention / Anonymization)
    elif category_code in ("inactive-patients", "patients"):
        query = select(Patient).where(
            Patient.is_active.is_(False),
            Patient.updated_at < cutoff,
        )
        if held_patient_ids:
            query = query.where(Patient.id.not_in(held_patient_ids))

        candidates = db.execute(query).scalars().all()
        candidate_count = len(candidates)

        if action == "anonymise":
            if not dry_run:
                for patient in candidates:
                    patient.full_name = "ANONYMIZED"
                    patient.phone_number = "0000000000"
                    patient.email = None
                    patient.address = None
                    patient.village = None
                    patient.city = None
                    patient.district = None
                    patient.state = None
                    patient.pincode = None
                    patient.abha_number = None
                affected_count = candidate_count
            else:
                affected_count = candidate_count

        elif action == "delete":
            if not dry_run and candidates:
                patient_ids = [p.id for p in candidates]
                db.execute(delete(Patient).where(Patient.id.in_(patient_ids)))
                affected_count = len(patient_ids)
            else:
                affected_count = candidate_count

        return {
            "category_code": category_code,
            "action": action,
            "cutoff": cutoff.isoformat(),
            "candidate_count": candidate_count,
            "held_count": len(held_patient_ids),
            "affected_count": affected_count,
            "status": "SUCCESS",
        }

    # Default / Generic Category (e.g. synthetic-attachments, custom categories)
    return {
        "category_code": category_code,
        "action": action,
        "cutoff": cutoff.isoformat(),
        "candidate_count": 0,
        "held_count": 0,
        "affected_count": 0,
        "status": "NOOP_UNKNOWN_CATEGORY",
    }


def execute_retention_policy_revision(
    db: Session,
    *,
    revision: PrivacyPolicyRevision,
    category_codes: list[str],
    actor: User,
    approval_reference: str,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Validates and executes retention actions for the requested categories
    under an approved retention policy revision.
    """
    now = now or datetime.now(timezone.utc)
    if revision.policy_type != "RETENTION" or revision.status != "APPROVED":
        raise ValueError("Only an approved retention policy can be executed.")

    policy: RetentionPolicyV1 = validate_policy_document(
        "RETENTION", revision.policy_document
    )
    rule_map = {rule.code: rule for rule in policy.categories}

    results = []
    total_affected = 0
    total_held = 0

    for code in category_codes:
        rule = rule_map.get(code)
        if not rule:
            continue
        res = execute_retention_category(
            db,
            category_code=rule.code,
            action=rule.action,
            retention_days=rule.retention_days,
            clock_start=rule.clock_start,
            now=now,
            dry_run=dry_run,
        )
        total_affected += res.get("affected_count", 0)
        total_held += res.get("held_count", 0)
        results.append(res)

    if not dry_run:
        # Record audit entry for the retention execution
        db.add(
            PrivacyDecisionAudit(
                policy_revision_id=revision.id,
                actor_user_id=actor.id,
                data_use_point="privacy.retention.execute",
                purpose_code=None,
                mode=revision.enforcement_mode,
                result="ALLOW",
                reason_code=f"retention_executed_{approval_reference[:50]}",
                occurred_at=now,
            )
        )
        db.commit()

    return {
        "policy_revision_id": revision.id,
        "dry_run": dry_run,
        "executed_at": now.isoformat(),
        "approval_reference": approval_reference,
        "total_affected": total_affected,
        "total_held": total_held,
        "categories": results,
    }

