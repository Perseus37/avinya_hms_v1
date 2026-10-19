from datetime import datetime, timedelta, timezone
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db, get_redis, resolve_tenant_context
from backend.core.dependencies import RequirePermission, get_current_user
from backend.domains.identity.models import User
from backend.domains.patients.models import (
    AllergyMaster,
    BreakGlassGrant,
    BreakGlassUse,
    Patient,
    PatientAllergy,
)

router = APIRouter(prefix="/api/break-glass", tags=["Emergency Patient Access"])
logger = logging.getLogger("avinya.break_glass")


class BreakGlassRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 10:
            raise ValueError("Emergency access reason must contain at least 10 non-space characters.")
        return cleaned


class BreakGlassReview(BaseModel):
    outcome: str = Field(pattern=r"^(JUSTIFIED|UNJUSTIFIED)$")
    notes: str = Field(min_length=5, max_length=1000)

    @field_validator("notes")
    @classmethod
    def strip_notes(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 5:
            raise ValueError("Review notes must contain at least 5 non-space characters.")
        return cleaned


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@router.post("/patients/{patient_id}/grants", status_code=status.HTTP_201_CREATED)
def request_emergency_access(
    patient_id: int,
    payload: BreakGlassRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("break_glass:request")),
    redis_client=Depends(get_redis),
):
    now = datetime.now(timezone.utc)
    db.execute(
        select(User).where(User.id == current_user.id).with_for_update()
    ).scalar_one()
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    recent_grants = db.execute(
        select(func.count(BreakGlassGrant.id)).where(
            BreakGlassGrant.user_id == current_user.id,
            BreakGlassGrant.created_at >= since,
        )
    ).scalar_one()
    if recent_grants >= settings.BREAK_GLASS_MAX_REQUESTS_PER_DAY:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Emergency access request limit reached for today.",
        )
    patient = db.get(Patient, patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Patient not found.")
    grant = BreakGlassGrant(
        patient_id=patient_id,
        user_id=current_user.id,
        reason=payload.reason.strip(),
        expires_at=now + timedelta(minutes=30),
        review_due_at=now + timedelta(hours=24),
    )
    db.add(grant)
    db.flush()
    tenant = resolve_tenant_context(request)
    db.commit()
    try:
        redis_client.publish(
            f"tenant:{tenant.subdomain_slug}:break_glass",
            json.dumps({"type": "break_glass.access_requested", "grant_id": grant.id}),
        )
    except Exception:
        logger.exception(
            "Failed to publish break-glass grant notification for grant %s.",
            grant.id,
        )
    return {
        "grant_id": grant.id,
        "patient_id": grant.patient_id,
        "expires_at": grant.expires_at,
        "review_due_at": grant.review_due_at,
        "status": "ACTIVE",
    }


@router.get("/grants/{grant_id}/patient")
def read_emergency_patient_record(
    grant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    grant = db.get(BreakGlassGrant, grant_id)
    now = datetime.now(timezone.utc)
    if (
        not grant
        or grant.user_id != current_user.id
        or _utc(grant.expires_at) <= now
    ):
        raise HTTPException(status_code=403, detail="Emergency access grant is unavailable or expired.")
    patient = db.get(Patient, grant.patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Patient not found.")
    allergies = db.execute(
        select(PatientAllergy, AllergyMaster)
        .join(AllergyMaster, PatientAllergy.allergy_id == AllergyMaster.id)
        .where(
            PatientAllergy.patient_id == patient.id,
            PatientAllergy.is_active.is_(True),
        )
    ).all()
    db.add(BreakGlassUse(grant_id=grant.id, user_id=current_user.id))
    db.commit()
    return {
        "patient": {
            "id": patient.id,
            "mrn": patient.mrn,
            "full_name": patient.full_name,
            "date_of_birth": patient.date_of_birth,
            "gender": patient.gender.value,
            "blood_group": patient.blood_group.value if patient.blood_group else None,
            "phone_number": patient.phone_number,
            "allergies": [
                {
                    "name": allergy.name,
                    "severity": patient_allergy.severity.value,
                    "reaction": patient_allergy.reaction_description,
                }
                for patient_allergy, allergy in allergies
            ],
        },
        "grant_expires_at": grant.expires_at,
    }


@router.get("/reviews/pending")
def list_pending_reviews(
    db: Session = Depends(get_db),
    reviewer: User = Depends(RequirePermission("iam:write")),
):
    now = datetime.now(timezone.utc)
    grants = db.execute(
        select(BreakGlassGrant)
        .where(BreakGlassGrant.reviewed_at.is_(None))
        .order_by(BreakGlassGrant.created_at.asc())
        .limit(200)
    ).scalars().all()
    return {
        "items": [
            {
                "grant_id": grant.id,
                "patient_id": grant.patient_id,
                "user_id": grant.user_id,
                "reason": grant.reason,
                "created_at": grant.created_at,
                "review_due_at": grant.review_due_at,
                "overdue": _utc(grant.review_due_at) < now,
            }
            for grant in grants
        ]
    }


@router.post("/reviews/{grant_id}")
def review_emergency_access(
    grant_id: int,
    payload: BreakGlassReview,
    db: Session = Depends(get_db),
    reviewer: User = Depends(RequirePermission("iam:write")),
):
    grant = db.execute(
        select(BreakGlassGrant)
        .where(BreakGlassGrant.id == grant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not grant:
        raise HTTPException(status_code=404, detail="Emergency access record not found.")
    if grant.user_id == reviewer.id:
        raise HTTPException(status_code=403, detail="Operators cannot review their own emergency access.")
    if grant.reviewed_at is not None:
        raise HTTPException(status_code=409, detail="Emergency access has already been reviewed.")
    grant.reviewed_at = datetime.now(timezone.utc)
    grant.reviewed_by_user_id = reviewer.id
    grant.review_outcome = payload.outcome
    grant.review_notes = payload.notes.strip()
    db.commit()
    return {
        "grant_id": grant.id,
        "reviewed_at": grant.reviewed_at,
        "review_outcome": grant.review_outcome,
    }
