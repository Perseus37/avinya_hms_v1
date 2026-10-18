from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.patients.models import Patient
from backend.domains.privacy.models import PatientConsentRecord, PrivacyRetentionPolicy

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
