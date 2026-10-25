from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.quality.models import (
    ClinicalIncidentSeverityEnum,
    ClinicalIncidentStatusEnum,
    ClinicalIncidentTypeEnum,
    ClinicalQualityIncident,
    QualityIndicatorBenchmark,
)
from backend.domains.quality.schemas import (
    ClinicalIncidentCreate,
    ClinicalIncidentResponse,
    ClinicalIncidentUpdate,
    QualityBenchmarkCreate,
    QualityBenchmarkResponse,
    QualityIndicatorsSummaryResponse,
)
from backend.domains.quality.services import (
    calculate_quality_indicators,
    ensure_default_benchmarks,
    record_quality_incident,
)

router = APIRouter(prefix="/api/quality", tags=["Clinical Quality & NABH/JCI Accreditations"])


@router.post("/incidents", response_model=ClinicalIncidentResponse, status_code=status.HTTP_201_CREATED)
def report_clinical_incident(
    payload: ClinicalIncidentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("clinical:write", "nursing:write", "opd:write", "ipd:write")),
):
    """Reports a patient safety, adverse drug reaction, or healthcare-associated infection incident."""
    incident = record_quality_incident(db, payload, actor_id=current_user.id)
    return incident


@router.get("/incidents", response_model=List[ClinicalIncidentResponse])
def list_clinical_incidents(
    incident_type: Optional[ClinicalIncidentTypeEnum] = None,
    severity: Optional[ClinicalIncidentSeverityEnum] = None,
    status_filter: Optional[ClinicalIncidentStatusEnum] = Query(default=None, alias="status"),
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("clinical:read", "nursing:read", "opd:read")),
):
    """Lists clinical quality and patient safety incidents."""
    stmt = select(ClinicalQualityIncident)
    if incident_type:
        stmt = stmt.where(ClinicalQualityIncident.incident_type == incident_type)
    if severity:
        stmt = stmt.where(ClinicalQualityIncident.severity == severity)
    if status_filter:
        stmt = stmt.where(ClinicalQualityIncident.status == status_filter)
    stmt = stmt.order_by(ClinicalQualityIncident.reported_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


@router.get("/incidents/{incident_id}", response_model=ClinicalIncidentResponse)
def get_clinical_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("clinical:read", "nursing:read", "opd:read", "ipd:read", "emr:read")),
):
    """Retrieves full details of a specific clinical quality incident."""
    incident = db.get(ClinicalQualityIncident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Clinical quality incident not found.")
    return incident


@router.patch("/incidents/{incident_id}", response_model=ClinicalIncidentResponse)
def update_clinical_incident(
    incident_id: int,
    payload: ClinicalIncidentUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("clinical:write", "nursing:write", "opd:write", "ipd:write", "emr:write")),
):
    """Updates status, root cause analysis, and CAPA plan for an incident."""
    incident = db.get(ClinicalQualityIncident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Clinical quality incident not found.")

    if payload.severity is not None:
        incident.severity = payload.severity
    if payload.status is not None:
        incident.status = payload.status
        if payload.status in {ClinicalIncidentStatusEnum.RESOLVED, ClinicalIncidentStatusEnum.CLOSED}:
            incident.resolved_at = datetime.now(timezone.utc)
    if payload.root_cause_analysis is not None:
        incident.root_cause_analysis = payload.root_cause_analysis
    if payload.capa_plan is not None:
        incident.capa_plan = payload.capa_plan
    if payload.device_days is not None:
        incident.device_days = payload.device_days

    db.commit()
    db.refresh(incident)
    return incident


@router.get("/indicators/summary", response_model=QualityIndicatorsSummaryResponse)
def get_quality_indicators_summary(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("clinical:read", "analytics:read", "opd:read")),
):
    """
    Computes hospital-wide clinical quality accreditation indicators
    (CAUTI, CLABSI, VAP, SSI, Falls, Med Errors, Unplanned ICU/OT returns)
    against NABH 5th Edition and JCI 7th Edition benchmark thresholds.
    """
    now = datetime.now(timezone.utc)
    end_at = end_date or now
    start_at = start_date or (end_at - timedelta(days=30))

    summary = calculate_quality_indicators(db, start_at=start_at, end_at=end_at)
    return summary


@router.get("/benchmarks", response_model=List[QualityBenchmarkResponse])
def list_quality_benchmarks(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("clinical:read", "mdm:read", "opd:read")),
):
    """Lists accreditation indicator benchmark thresholds."""
    ensure_default_benchmarks(db)
    stmt = select(QualityIndicatorBenchmark).where(QualityIndicatorBenchmark.is_active.is_(True))
    return list(db.execute(stmt).scalars().all())


@router.post("/benchmarks", response_model=QualityBenchmarkResponse, status_code=status.HTTP_201_CREATED)
def create_quality_benchmark(
    payload: QualityBenchmarkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    """Creates or customizes an accreditation quality indicator benchmark threshold."""
    existing = db.execute(
        select(QualityIndicatorBenchmark).where(
            QualityIndicatorBenchmark.indicator_code == payload.indicator_code
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Benchmark '{payload.indicator_code}' already exists.")

    benchmark = QualityIndicatorBenchmark(**payload.model_dump(), created_by_id=current_user.id)
    db.add(benchmark)
    db.commit()
    db.refresh(benchmark)
    return benchmark
