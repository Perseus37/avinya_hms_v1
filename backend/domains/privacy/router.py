from datetime import date, datetime, timezone
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.patients.models import Patient
from backend.domains.privacy.models import (
    PatientConsentRecord,
    PrivacyLegalHold,
    PrivacyPolicyRevision,
    PrivacyRetentionPolicy,
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
ConsentSource = Literal["WRITTEN", "VERBAL", "DIGITAL", "GUARDIAN"]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class ConsentRecordCreate(BaseModel):
    consent_type: ConsentType
    purpose: str = Field(min_length=3, max_length=255)
    decision: ConsentDecision
    source: ConsentSource
    evidence_reference: str | None = Field(default=None, max_length=100)
    expires_at: datetime | None = None

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


class PrivacyPolicyApproval(BaseModel):
    approval_reference: str = Field(min_length=3, max_length=100)

    @field_validator("approval_reference")
    @classmethod
    def normalize_approval_reference(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Approval reference cannot be blank.")
        return cleaned


class PrivacyLegalHoldCreate(BaseModel):
    patient_id: int | None = Field(default=None, gt=0)
    data_category: str | None = Field(default=None, min_length=2, max_length=50)
    reason: str = Field(min_length=5, max_length=2000)
    hold_reference: str = Field(min_length=3, max_length=100)
    expires_at: datetime | None = None

    @field_validator("data_category", "reason", "hold_reference")
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

    latest = db.execute(
        select(PatientConsentRecord)
        .where(
            PatientConsentRecord.patient_id == patient_id,
            PatientConsentRecord.consent_type == payload.consent_type,
            PatientConsentRecord.purpose == payload.purpose,
        )
        .order_by(
            PatientConsentRecord.recorded_at.desc(),
            PatientConsentRecord.id.desc(),
        )
        .limit(1)
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

    record = PatientConsentRecord(
        patient_id=patient_id,
        **payload.model_dump(),
        recorded_by_user_id=actor.id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
