from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.patients.models import Patient
from backend.domains.privacy.data_use_points import DATA_USE_POINTS
from backend.domains.privacy.decision_engine import counts_only_report
from backend.domains.privacy.decision_engine import SOURCE_TO_CAPTURE
from backend.domains.privacy.models import (
    PatientConsentRecord,
    PrivacyDecisionAudit,
    PrivacyLegalHold,
    PrivacyPolicyRevision,
    PrivacyRetentionPolicy,
)
from backend.domains.privacy.policy_schema import validate_policy_document
from backend.domains.privacy.retention_service import (
    execute_retention_policy_revision,
)
from backend.domains.privacy.retention_preview import (
    get_allowed_retention_categories,
    preview_retention_counts,
    retention_category_registry,
)

router = APIRouter(prefix="/api/privacy", tags=["Privacy Governance"])

ConsentType = Literal[
    "CARE",
    "DATA_SHARING",
    "RESEARCH",
    "COMMUNICATIONS",
    "TELEMEDICINE",
    "DATA_PROCESSING",
]
ConsentDecision = Literal["GRANTED", "DECLINED", "WITHDRAWN"]
ConsentSource = Literal[
    "WRITTEN",
    "VERBAL",
    "DIGITAL",
    "GUARDIAN",
    "E_SIGNATURE",
    "OTP",
    "DIGITAL_OPT_IN",
    "WITNESSED_VERBAL",
    "GUARDIAN_SIGNATURE",
    "ABDM_ARTIFACT",
]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class ConsentRecordCreate(BaseModel):
    consent_type: ConsentType
    purpose: str = Field(min_length=3, max_length=255)
    decision: ConsentDecision
    source: ConsentSource
    evidence_reference: str | None = Field(default=None, max_length=100)
    expires_at: datetime | None = None
    scope_type: Literal[
        "patient", "episode", "recipient", "study", "channel", "item"
    ] = "patient"
    scope_key: str | None = Field(default=None, min_length=1, max_length=255)
    consenter_type: Literal["patient", "guardian", "representative"] = "patient"
    form_version: str | None = Field(default=None, max_length=100)

    @field_validator("purpose", "evidence_reference")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be blank.")
        return cleaned


class RetentionPolicyCreate(BaseModel):
    data_category: str = Field(min_length=2, max_length=50)
    retention_days: int = Field(gt=0, le=36500)
    legal_basis: str = Field(min_length=5, max_length=2000)
    approval_reference: str = Field(min_length=3, max_length=100)
    effective_from: date

    @field_validator("data_category", "legal_basis", "approval_reference")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be blank.")
        return cleaned


class PrivacyPolicyDraftCreate(BaseModel):
    policy_type: Literal["CONSENT", "RETENTION"]
    policy_document: dict[str, Any] = Field(min_length=1)

    @field_validator("policy_document")
    @classmethod
    def limit_policy_document_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=True).encode("utf-8")) > 32768:
            raise ValueError("Policy document must not exceed 32 KiB.")
        return value

    @field_validator("policy_document")
    @classmethod
    def validate_versioned_policy(cls, value: dict[str, Any], info):
        policy_type = info.data.get("policy_type")
        if policy_type is not None:
            validate_policy_document(policy_type, value)
        return value


class PrivacyPolicyApproval(BaseModel):
    approval_reference: str = Field(min_length=3, max_length=100)

    @field_validator("approval_reference")
    @classmethod
    def normalize_approval_reference(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Approval reference cannot be blank.")
        return cleaned


class PrivacyPolicyModeApproval(BaseModel):
    approval_reference: str = Field(min_length=3, max_length=100)
    carry_over_from_revision_id: int | None = Field(default=None, gt=0)
    carry_over_purposes: list[str] | None = None

    @field_validator("approval_reference")
    @classmethod
    def normalize_mode_approval_reference(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Approval reference cannot be blank.")
        return cleaned



class RetentionExecutionRequest(BaseModel):
    policy_revision_id: int = Field(gt=0)
    categories: list[str] = Field(min_length=1, max_length=100)
    approval_reference: str = Field(min_length=3, max_length=100)
    dry_run: bool = Field(default=False)

    @field_validator("approval_reference")
    @classmethod
    def normalize_approval_reference(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Approval reference cannot be blank.")
        return cleaned

class RetentionDryRunRequest(BaseModel):
    policy_revision_id: int = Field(gt=0)
    categories: list[str] = Field(min_length=1, max_length=100)


class PrivacyLegalHoldCreate(BaseModel):
    patient_id: int | None = Field(default=None, gt=0)
    data_category: str | None = Field(default=None, min_length=2, max_length=50)
    reason: str = Field(min_length=5, max_length=2000)
    hold_reference: str = Field(min_length=3, max_length=100)
    expires_at: datetime | None = None

    @field_validator("data_category")
    @classmethod
    def normalize_and_validate_data_category(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Value cannot be blank.")
        allowed = get_allowed_retention_categories()
        if cleaned not in allowed:
            raise ValueError(
                f"data_category '{cleaned}' is not a recognized retention category. Allowed: {sorted(allowed)}"
            )
        return cleaned

    @field_validator("reason", "hold_reference")
    @classmethod
    def normalize_hold_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be blank.")
        return cleaned

    @field_validator("expires_at")
    @classmethod
    def require_aware_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone.")
        return value


class PrivacyLegalHoldRelease(BaseModel):
    release_reference: str = Field(min_length=3, max_length=100)

    @field_validator("release_reference")
    @classmethod
    def normalize_release_reference(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Release reference cannot be blank.")
        return cleaned


@router.get("/policies")
def list_privacy_policy_revisions(
    policy_type: Literal["CONSENT", "RETENTION"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("privacy:read")),
):
    statement = select(PrivacyPolicyRevision).order_by(
        PrivacyPolicyRevision.id.desc()
    )
    if policy_type:
        statement = statement.where(
            PrivacyPolicyRevision.policy_type == policy_type
        )
    items = db.execute(statement.limit(limit)).scalars().all()
    return {"items": items}


@router.post("/policies", status_code=201)
def create_privacy_policy_draft(
    payload: PrivacyPolicyDraftCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:write")),
):
    revision = PrivacyPolicyRevision(
        policy_type=payload.policy_type,
        policy_document=payload.policy_document,
        configured_by_user_id=actor.id,
    )
    db.add(revision)
    db.commit()
    db.refresh(revision)
    return revision


@router.get("/data-use-points")
def list_privacy_data_use_points(
    _: User = Depends(RequirePermission("privacy:read")),
):
    return {
        "items": [
            {
                "code": point.code,
                "description": point.description,
                "route": point.route,
            }
            for point in sorted(DATA_USE_POINTS.values(), key=lambda item: item.code)
        ]
    }


@router.post("/policies/{revision_id}/activate-audit-only")
def activate_privacy_policy_audit_only(
    revision_id: int,
    payload: PrivacyPolicyModeApproval,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:approve")),
):
    if not settings.PRIVACY_ALLOW_AUDIT_ONLY:
        raise HTTPException(
            status_code=409,
            detail="Audit-only mode is disabled by deployment configuration.",
        )
    if settings.ENVIRONMENT.strip().lower() in {"prod", "production"}:
        raise HTTPException(
            status_code=409,
            detail="Audit-only mode cannot be activated in production.",
        )

    revision = db.execute(
        select(PrivacyPolicyRevision)
        .where(PrivacyPolicyRevision.id == revision_id)
        .with_for_update()
    ).scalar_one_or_none()
    if revision is None:
        raise HTTPException(status_code=404, detail="Privacy policy revision not found.")
    if revision.policy_type != "CONSENT" or revision.status != "APPROVED":
        raise HTTPException(
            status_code=409,
            detail="Only an approved consent policy can enter audit-only mode.",
        )
    if revision.configured_by_user_id == actor.id:
        raise HTTPException(
            status_code=409,
            detail="A different user must authorize audit-only activation.",
        )
    try:
        validate_policy_document(revision.policy_type, revision.policy_document)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    for active in db.execute(
        select(PrivacyPolicyRevision)
        .where(PrivacyPolicyRevision.is_active.is_(True))
        .with_for_update()
    ).scalars():
        active.is_active = False
        active.enforcement_mode = "OFF"
        active.mode_set_by_user_id = None
        active.mode_set_at = None
        active.mode_approval_reference = None

    now = datetime.now(timezone.utc)
    revision.is_active = True
    revision.enforcement_mode = "AUDIT_ONLY"
    revision.mode_set_by_user_id = actor.id
    revision.mode_set_at = now
    revision.mode_approval_reference = payload.approval_reference

    # Carry over active consents from prior revision if requested (F-05)
    if payload.carry_over_from_revision_id:
        prior_revision = db.get(PrivacyPolicyRevision, payload.carry_over_from_revision_id)
        if prior_revision is None:
            raise HTTPException(status_code=404, detail="Prior policy revision for carry-over not found.")
        validate_policy_document("CONSENT", prior_revision.policy_document)
        new_policy = validate_policy_document("CONSENT", revision.policy_document)
        new_purpose_codes = {p.code for p in new_policy.purposes}

        allowed_purposes = (
            set(payload.carry_over_purposes)
            if payload.carry_over_purposes is not None
            else new_purpose_codes
        )
        eligible_purposes = allowed_purposes.intersection(new_purpose_codes)

        prior_consents = db.execute(
            select(PatientConsentRecord).where(
                PatientConsentRecord.policy_revision_id == prior_revision.id,
                PatientConsentRecord.decision == "GRANTED",
                PatientConsentRecord.purpose.in_(eligible_purposes),
            )
        ).scalars().all()

        for old_c in prior_consents:
            if old_c.expires_at is not None and old_c.expires_at <= now:
                continue
            db.add(
                PatientConsentRecord(
                    patient_id=old_c.patient_id,
                    consent_type=old_c.consent_type,
                    purpose=old_c.purpose,
                    decision="GRANTED",
                    source=old_c.source,
                    evidence_reference=f"CARRYOVER:{old_c.evidence_reference or 'LEGACY'}",
                    scope_type=old_c.scope_type,
                    scope_key=old_c.scope_key,
                    consenter_type=old_c.consenter_type,
                    policy_revision_id=revision.id,
                    form_version=old_c.form_version,
                    expires_at=old_c.expires_at,
                    recorded_by_user_id=actor.id,
                    recorded_at=now,
                )
            )

    db.add(
        PrivacyDecisionAudit(
            policy_revision_id=revision.id,
            actor_user_id=actor.id,
            data_use_point="privacy.policy.mode",
            purpose_code=None,
            mode="AUDIT_ONLY",
            result="ALLOW",
            reason_code="audit_only_activated",
            occurred_at=now,
        )
    )
    db.commit()
    db.refresh(revision)
    return revision


@router.post("/policies/{revision_id}/deactivate")
def deactivate_privacy_policy(
    revision_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:approve")),
):
    revision = db.execute(
        select(PrivacyPolicyRevision)
        .where(PrivacyPolicyRevision.id == revision_id)
        .with_for_update()
    ).scalar_one_or_none()
    if revision is None:
        raise HTTPException(status_code=404, detail="Privacy policy revision not found.")
    if revision.is_active:
        revision.is_active = False
        revision.enforcement_mode = "OFF"
        revision.mode_set_by_user_id = None
        revision.mode_set_at = None
        revision.mode_approval_reference = None
        db.add(
            PrivacyDecisionAudit(
                policy_revision_id=revision.id,
                actor_user_id=actor.id,
                data_use_point="privacy.policy.mode",
                purpose_code=None,
                mode="OFF",
                result="ALLOW",
                reason_code="audit_only_deactivated",
                occurred_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        db.refresh(revision)
    return revision


@router.get("/decision-audits")
def list_privacy_decision_audit_counts(
    since: datetime | None = None,
    until: datetime | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("privacy:read")),
):
    now = datetime.now(timezone.utc)
    until = until or now
    since = since or until - timedelta(days=30)
    if since.tzinfo is None or until.tzinfo is None or since > until:
        raise HTTPException(
            status_code=422,
            detail="since and until must be timezone-aware and since must not exceed until.",
        )
    if until - since > timedelta(days=31):
        raise HTTPException(status_code=422, detail="Report range must not exceed 31 days.")
    rows = db.execute(
        select(
            PrivacyDecisionAudit.data_use_point,
            PrivacyDecisionAudit.result,
            PrivacyDecisionAudit.reason_code,
            func.count(PrivacyDecisionAudit.id),
        )
        .where(
            PrivacyDecisionAudit.occurred_at >= since,
            PrivacyDecisionAudit.occurred_at <= until,
            PrivacyDecisionAudit.data_use_point.in_(DATA_USE_POINTS),
        )
        .group_by(
            PrivacyDecisionAudit.data_use_point,
            PrivacyDecisionAudit.result,
            PrivacyDecisionAudit.reason_code,
        )
    ).all()
    return {"since": since, "until": until, "items": counts_only_report(rows)}


@router.post("/policies/{revision_id}/approve")
def approve_privacy_policy_draft(
    revision_id: int,
    payload: PrivacyPolicyApproval,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:approve")),
):
    revision = db.execute(
        select(PrivacyPolicyRevision)
        .where(PrivacyPolicyRevision.id == revision_id)
        .with_for_update()
    ).scalar_one_or_none()
    if revision is None:
        raise HTTPException(status_code=404, detail="Privacy policy draft not found.")
    if revision.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only a draft can be approved.")
    if revision.configured_by_user_id == actor.id:
        raise HTTPException(
            status_code=409,
            detail="A different user must approve the privacy policy draft.",
        )
    try:
        validate_policy_document(revision.policy_type, revision.policy_document)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Privacy policy validation failed: {exc}",
        ) from exc

    revision.status = "APPROVED"
    revision.approved_by_user_id = actor.id
    revision.approved_at = datetime.now(timezone.utc)
    revision.approval_reference = payload.approval_reference
    db.commit()
    db.refresh(revision)
    return revision


@router.get("/legal-holds")
def list_privacy_legal_holds(
    active_only: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("privacy:read")),
):
    statement = select(PrivacyLegalHold).order_by(PrivacyLegalHold.id.desc())
    if active_only:
        now = datetime.now(timezone.utc)
        statement = statement.where(
            PrivacyLegalHold.released_at.is_(None),
            (PrivacyLegalHold.expires_at.is_(None))
            | (PrivacyLegalHold.expires_at > now),
        )
    return {"items": db.execute(statement.limit(limit)).scalars().all()}


@router.post("/legal-holds", status_code=201)
def create_privacy_legal_hold(
    payload: PrivacyLegalHoldCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:write")),
):
    if payload.patient_id is None and payload.data_category is None:
        raise HTTPException(
            status_code=422,
            detail="A patient or data category scope is required.",
        )
    if payload.patient_id is not None and db.get(Patient, payload.patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if payload.expires_at is not None and payload.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="expires_at must be in the future.")

    hold = PrivacyLegalHold(
        **payload.model_dump(),
        created_by_user_id=actor.id,
    )
    db.add(hold)
    db.commit()
    db.refresh(hold)
    return hold


@router.post("/legal-holds/{hold_id}/release")
def release_privacy_legal_hold(
    hold_id: int,
    payload: PrivacyLegalHoldRelease,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:approve")),
):
    hold = db.execute(
        select(PrivacyLegalHold)
        .where(PrivacyLegalHold.id == hold_id)
        .with_for_update()
    ).scalar_one_or_none()
    if hold is None:
        raise HTTPException(status_code=404, detail="Legal hold not found.")
    if hold.released_at is not None:
        raise HTTPException(status_code=409, detail="Legal hold is already released.")
    if hold.created_by_user_id == actor.id:
        raise HTTPException(
            status_code=409,
            detail="A different user must release the legal hold.",
        )

    hold.released_by_user_id = actor.id
    hold.released_at = datetime.now(timezone.utc)
    hold.release_reference = payload.release_reference
    db.commit()
    db.refresh(hold)
    return hold


@router.get("/retention-policies")
def list_retention_policies(
    data_category: str | None = Query(default=None, min_length=2, max_length=50),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("privacy:read")),
):
    statement = select(PrivacyRetentionPolicy).order_by(
        PrivacyRetentionPolicy.id.desc()
    )
    if data_category:
        statement = statement.where(
            PrivacyRetentionPolicy.data_category == data_category.strip()
        )
    items = db.execute(statement.limit(limit)).scalars().all()
    return {"items": items}


@router.post("/retention/dry-run")
def create_retention_dry_run(
    payload: RetentionDryRunRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("privacy:approve")),
):
    if (
        not settings.PRIVACY_ALLOW_RETENTION_DRY_RUN
        or settings.ENVIRONMENT.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(
            status_code=409,
            detail="Retention dry-run is disabled by deployment configuration.",
        )
    revision = db.get(PrivacyPolicyRevision, payload.policy_revision_id)
    if (
        revision is None
        or revision.policy_type != "RETENTION"
        or revision.status != "APPROVED"
    ):
        raise HTTPException(
            status_code=409,
            detail="A second-person-approved retention policy is required.",
        )
    try:
        policy = validate_policy_document("RETENTION", revision.policy_document)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Approved retention policy cannot be validated: {exc}",
        ) from exc
    policy_categories = {item.code for item in policy.categories}
    requested = set(payload.categories)
    if not requested.issubset(policy_categories):
        raise HTTPException(
            status_code=422,
            detail="Requested categories must be present in the approved policy.",
        )
    try:
        items = preview_retention_counts(
            retention_category_registry, requested
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "policy_revision_id": revision.id,
        "generated_at": datetime.now(timezone.utc),
        "items": items,
        "destructive_action_performed": False,
    }


@router.post("/retention/execute")
def execute_retention_run(
    payload: RetentionExecutionRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:approve")),
):
    revision = db.get(PrivacyPolicyRevision, payload.policy_revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Privacy policy revision not found.")
    if revision.policy_type != "RETENTION" or revision.status != "APPROVED":
        raise HTTPException(status_code=409, detail="Only an approved retention policy can be executed.")
    if revision.configured_by_user_id == actor.id:
        raise HTTPException(status_code=409, detail="A different user must authorize retention execution.")
    try:
        policy = validate_policy_document("RETENTION", revision.policy_document)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"Approved retention policy cannot be validated: {exc}") from exc

    policy_categories = {item.code for item in policy.categories}
    requested = set(payload.categories)
    if not requested.issubset(policy_categories):
        raise HTTPException(status_code=422, detail="Requested categories must be present in the approved policy.")

    return execute_retention_policy_revision(
        db,
        revision=revision,
        category_codes=payload.categories,
        actor=actor,
        approval_reference=payload.approval_reference,
        dry_run=payload.dry_run,
    )



@router.post("/retention-policies", status_code=201)
def create_retention_policy(
    payload: RetentionPolicyCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:write")),
):
    policy = PrivacyRetentionPolicy(
        **payload.model_dump(),
        configured_by_user_id=actor.id,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.get("/patients/{patient_id}/consents")
def list_patient_consents(
    patient_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("privacy:read")),
):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    items = db.execute(
        select(PatientConsentRecord)
        .where(PatientConsentRecord.patient_id == patient_id)
        .order_by(PatientConsentRecord.recorded_at, PatientConsentRecord.id)
    ).scalars().all()
    return {"items": items}


@router.post("/patients/{patient_id}/consents", status_code=201)
def record_patient_consent(
    patient_id: int,
    payload: ConsentRecordCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("privacy:write")),
):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    now = datetime.now(timezone.utc)
    if payload.expires_at is not None:
        if payload.expires_at.tzinfo is None:
            raise HTTPException(status_code=422, detail="expires_at must include a timezone.")
        if payload.expires_at <= now:
            raise HTTPException(status_code=422, detail="expires_at must be in the future.")

    active_policy = db.execute(
        select(PrivacyPolicyRevision).where(
            PrivacyPolicyRevision.is_active.is_(True),
            PrivacyPolicyRevision.policy_type == "CONSENT",
        )
    ).scalar_one_or_none()
    purpose_rule = None
    policy_revision_id = None
    if active_policy is not None:
        policy = validate_policy_document("CONSENT", active_policy.policy_document)
        purpose_rule = next(
            (item for item in policy.purposes if item.code == payload.purpose), None
        )
        if purpose_rule is not None:
            policy_revision_id = active_policy.id

    latest_query = select(PatientConsentRecord).where(
        PatientConsentRecord.patient_id == patient_id,
        PatientConsentRecord.consent_type == payload.consent_type,
        PatientConsentRecord.purpose == payload.purpose,
        PatientConsentRecord.scope_type == payload.scope_type,
        PatientConsentRecord.scope_key == payload.scope_key,
    )
    if policy_revision_id is not None:
        latest_query = latest_query.where(
            PatientConsentRecord.policy_revision_id == policy_revision_id
        )
    latest = db.execute(
        latest_query.order_by(
            PatientConsentRecord.recorded_at.desc(),
            PatientConsentRecord.id.desc(),
        ).limit(1)
    ).scalar_one_or_none()
    if payload.decision == "WITHDRAWN" and (
        latest is None
        or latest.decision != "GRANTED"
        or (latest.expires_at is not None and _utc(latest.expires_at) <= now)
    ):
        raise HTTPException(
            status_code=409,
            detail="Consent can only be withdrawn while a matching grant is current.",
        )
    if payload.decision == "GRANTED" and latest is not None and (
        latest.decision == "GRANTED"
        and (latest.expires_at is None or _utc(latest.expires_at) > now)
    ):
        raise HTTPException(
            status_code=409,
            detail="A matching consent grant is already current.",
        )

    if active_policy is not None and purpose_rule is not None:
        if payload.scope_type != purpose_rule.scope:
            raise HTTPException(
                status_code=422,
                detail="Consent scope does not match the active policy.",
            )
        if purpose_rule.scope != "patient" and payload.scope_key is None:
            raise HTTPException(
                status_code=422,
                detail="scope_key is required for this consent scope.",
            )
        if payload.decision == "GRANTED":
            if payload.consenter_type not in purpose_rule.consenters:
                raise HTTPException(
                    status_code=422,
                    detail="Consenter type is not allowed by the active policy.",
                )
            capture_method = SOURCE_TO_CAPTURE.get(payload.source)
            if capture_method not in purpose_rule.capture:
                raise HTTPException(
                    status_code=422,
                    detail="Consent capture method is not allowed by the active policy.",
                )
            if not payload.evidence_reference:
                raise HTTPException(
                    status_code=422,
                    detail="Consent evidence_reference is required by the active policy.",
                )
            if purpose_rule.validity == "fixed_term" and payload.expires_at is None:
                raise HTTPException(
                    status_code=422,
                    detail="expires_at is required by the active policy.",
                )
        if payload.decision == "WITHDRAWN":
            if latest is None or latest.policy_revision_id != active_policy.id:
                raise HTTPException(
                    status_code=409,
                    detail="Only a grant under the active policy can be withdrawn.",
                )

    record = PatientConsentRecord(
        patient_id=patient_id,
        **payload.model_dump(),
        policy_revision_id=policy_revision_id,
        recorded_by_user_id=actor.id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
