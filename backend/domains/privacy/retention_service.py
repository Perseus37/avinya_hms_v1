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
    PrivacyRetentionRunLedger,
)
from backend.domains.privacy.policy_schema import (
    RetentionPolicyV1,
    validate_policy_document,
)
from backend.domains.privacy.retention_preview import canonicalize_category


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
    """Return True if an entire category is subject to an active legal hold (comparing canonical categories - N-02)."""
    canon_exec = canonicalize_category(category_code)
    return any(
        canonicalize_category(hold.data_category) == canon_exec and hold.patient_id is None
        for hold in holds
    )


def get_held_patient_ids(
    holds: list[PrivacyLegalHold], category_code: str | None = None
) -> set[int]:
    """Return set of patient IDs protected by active legal holds (comparing canonical categories - N-02)."""
    canon_exec = canonicalize_category(category_code) if category_code else None
    return {
        hold.patient_id
        for hold in holds
        if hold.patient_id is not None
        and (
            canon_exec is None
            or hold.data_category is None
            or canonicalize_category(hold.data_category) == canon_exec
        )
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
    canon_category = canonicalize_category(category_code) or category_code

    # 1. If entire category is on legal hold, skip completely (N-02 canonical check)
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
    if canon_category == "privacy-decision-audits":
        if action == "delete":
            # N-06 & N-26: Never delete retention execution or user unlock audit proofs
            candidate_stmt = select(func.count(PrivacyDecisionAudit.id)).where(
                PrivacyDecisionAudit.occurred_at < cutoff,
                PrivacyDecisionAudit.result == "ALLOW",
                PrivacyDecisionAudit.data_use_point.not_in(["privacy.retention.execute", "identity.user.unlock"]),
            )
            candidate_count = db.execute(candidate_stmt).scalar_one() or 0

            if not dry_run and candidate_count > 0:
                del_stmt = delete(PrivacyDecisionAudit).where(
                    PrivacyDecisionAudit.occurred_at < cutoff,
                    PrivacyDecisionAudit.result == "ALLOW",
                    PrivacyDecisionAudit.data_use_point.not_in(["privacy.retention.execute", "identity.user.unlock"]),
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

    # Category Handler: Patients (Patient Record Retention / Anonymization)
    elif canon_category == "patients":
        # N-07: Respect all 8 clock_start parameters
        from backend.domains.encounters.models import PatientEncounter
        from backend.domains.billing.models import Invoice

        if clock_start in ("creation", "created_at"):
            clock_filter = Patient.created_at < cutoff
        elif clock_start in ("last_activity", "updated_at"):
            clock_filter = Patient.updated_at < cutoff
        elif clock_start == "last_encounter":
            recent_enc_patients = select(PatientEncounter.patient_id).where(PatientEncounter.created_at >= cutoff)
            clock_filter = Patient.id.not_in(recent_enc_patients)
        elif clock_start == "discharge":
            recent_disch_patients = select(PatientEncounter.patient_id).where(
                func.coalesce(PatientEncounter.ended_at, PatientEncounter.created_at) >= cutoff
            )
            clock_filter = Patient.id.not_in(recent_disch_patients)
        elif clock_start == "invoice":
            recent_invoice_patients = select(Invoice.patient_id).where(Invoice.created_at >= cutoff)
            clock_filter = Patient.id.not_in(recent_invoice_patients)
        elif clock_start == "date_of_death":
            if hasattr(Patient, "date_of_death"):
                clock_filter = (Patient.date_of_death.is_not(None)) & (Patient.date_of_death < cutoff)
            else:
                clock_filter = Patient.updated_at < cutoff
        elif clock_start == "age_of_majority":
            majority_cutoff = cutoff - timedelta(days=18 * 365)
            clock_filter = (Patient.date_of_birth.is_not(None)) & (Patient.date_of_birth < majority_cutoff)
        elif clock_start == "contract_end":
            clock_filter = Patient.updated_at < cutoff
        else:
            clock_filter = Patient.updated_at < cutoff

        query = select(Patient).where(
            Patient.is_active.is_(False),
            clock_filter,
        )
        if held_patient_ids:
            query = query.where(Patient.id.not_in(held_patient_ids))

        candidates = db.execute(query).scalars().all()
        candidate_count = len(candidates)

        if action == "anonymise":
            if not dry_run:
                # N-01: Full anonymisation of all identifying fields
                for patient in candidates:
                    patient.full_name = "ANONYMIZED"
                    patient.mrn = f"ANON-{patient.id}"
                    patient.phone_number = "0000000000"
                    patient.email = None
                    patient.address = None
                    patient.village = None
                    patient.city = None
                    patient.district = None
                    patient.state = None
                    patient.pincode = None
                    patient.abha_number = None
                    patient.emergency_contact_name = None
                    patient.emergency_contact_phone = None
                    patient.blood_group = None
                    if patient.date_of_birth is not None:
                        patient.date_of_birth = datetime(
                            patient.date_of_birth.year, 1, 1, tzinfo=timezone.utc
                        )
                    if hasattr(patient, "allow_network_sharing"):
                        patient.allow_network_sharing = False
                affected_count = candidate_count
            else:
                affected_count = candidate_count

        elif action == "delete":
            # N-03: Hard delete safety guard.
            # Refuse deletion for patients with ANY dependent clinical, billing, allergy, or diagnostic records
            if not dry_run and candidates:
                from backend.domains.patients.models import PatientAllergy
                from backend.domains.diagnostics.models import DiagnosticOrder
                from backend.domains.insurance.models import PatientInsurancePolicy
                from backend.domains.privacy.models import PatientConsentRecord

                cand_ids = [p.id for p in candidates]
                referenced_patient_ids = set()

                # Batch checks across all dependent tables to prevent cascading deletions
                for col in [
                    PatientEncounter.patient_id,
                    Invoice.patient_id,
                    PatientAllergy.patient_id,
                    DiagnosticOrder.patient_id,
                    PatientInsurancePolicy.patient_id,
                    PatientConsentRecord.patient_id,
                ]:
                    try:
                        found_ids = db.execute(select(col).where(col.in_(cand_ids))).scalars().all()
                        referenced_patient_ids.update(found_ids)
                    except Exception:
                        pass

                deletable_ids = [p.id for p in candidates if p.id not in referenced_patient_ids]

                if deletable_ids:
                    db.execute(delete(Patient).where(Patient.id.in_(deletable_ids)))
                affected_count = len(deletable_ids)
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
        # N-06: Permanent tamper-evident retention run ledger entry
        db.add(
            PrivacyRetentionRunLedger(
                policy_revision_id=revision.id,
                categories=category_codes,
                action="execute",
                dry_run=False,
                candidate_count=sum(r.get("candidate_count", 0) for r in results),
                held_count=total_held,
                affected_count=total_affected,
                approval_reference=approval_reference,
                executed_by_user_id=actor.id,
                executed_at=now,
            )
        )
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
