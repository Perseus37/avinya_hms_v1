from datetime import datetime, timezone
import random
import string
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.domains.encounters.models import InpatientAdmission
from backend.domains.surgery.models import SurgicalSchedule
from backend.domains.pharmacy.models import MedicationAdministrationLog
from backend.domains.quality.models import (
    ClinicalIncidentStatusEnum,
    ClinicalIncidentTypeEnum,
    ClinicalQualityIncident,
    QualityIndicatorBenchmark,
)
from backend.domains.quality.schemas import ClinicalIncidentCreate


def generate_incident_code(db: Session) -> str:
    """Generates unique incident tracking code format: QI-YYYYMM-XXXX"""
    now = datetime.now(timezone.utc)
    prefix = f"QI-{now.strftime('%Y%m')}"
    for _ in range(10):
        suffix = "".join(random.choices(string.digits, k=4))
        code = f"{prefix}-{suffix}"
        exists = db.scalar(
            select(func.count(ClinicalQualityIncident.id)).where(
                ClinicalQualityIncident.incident_code == code
            )
        )
        if not exists:
            return code
    return f"{prefix}-{int(now.timestamp())}"


def record_quality_incident(
    db: Session,
    payload: ClinicalIncidentCreate,
    actor_id: Optional[int] = None,
) -> ClinicalQualityIncident:
    """Records a new patient safety or infection control adverse incident."""
    code = generate_incident_code(db)
    incident = ClinicalQualityIncident(
        incident_code=code,
        incident_type=payload.incident_type,
        severity=payload.severity,
        patient_id=payload.patient_id,
        encounter_id=payload.encounter_id,
        ward_id=payload.ward_id,
        device_days=payload.device_days,
        description=payload.description,
        root_cause_analysis=payload.root_cause_analysis,
        capa_plan=payload.capa_plan,
        status=ClinicalIncidentStatusEnum.REPORTED,
        reported_at=datetime.now(timezone.utc),
        created_by_id=actor_id,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


DEFAULT_NABH_BENCHMARKS = [
    {
        "indicator_code": "CAUTI_RATE",
        "name": "Catheter-Associated Urinary Tract Infection Rate",
        "standard": "NABH_5TH_ED",
        "unit": "PER_1000_DEVICE_DAYS",
        "target_threshold": 2.0,
        "warning_threshold": 3.5,
        "description": "CAUTI cases per 1,000 urinary catheter days in inpatient wards and ICUs.",
    },
    {
        "indicator_code": "CLABSI_RATE",
        "name": "Central Line-Associated Bloodstream Infection Rate",
        "standard": "NABH_5TH_ED",
        "unit": "PER_1000_DEVICE_DAYS",
        "target_threshold": 1.5,
        "warning_threshold": 3.0,
        "description": "CLABSI cases per 1,000 central venous catheter days.",
    },
    {
        "indicator_code": "VAP_RATE",
        "name": "Ventilator-Associated Pneumonia Rate",
        "standard": "NABH_5TH_ED",
        "unit": "PER_1000_DEVICE_DAYS",
        "target_threshold": 2.0,
        "warning_threshold": 4.0,
        "description": "VAP cases per 1,000 mechanical ventilator days in ICUs.",
    },
    {
        "indicator_code": "SSI_RATE",
        "name": "Surgical Site Infection Rate",
        "standard": "NABH_5TH_ED",
        "unit": "PERCENT",
        "target_threshold": 1.5,
        "warning_threshold": 2.5,
        "description": "Percentage of clean/clean-contaminated surgical procedures developing SSI.",
    },
    {
        "indicator_code": "FALL_RATE",
        "name": "Inpatient Fall Rate",
        "standard": "JCI_7TH_ED",
        "unit": "PER_1000_BED_DAYS",
        "target_threshold": 1.5,
        "warning_threshold": 3.0,
        "description": "Total inpatient falls per 1,000 inpatient bed days.",
    },
    {
        "indicator_code": "MED_ERROR_RATE",
        "name": "Medication Administration Error Rate",
        "standard": "NABH_5TH_ED",
        "unit": "PER_1000_DOSES",
        "target_threshold": 5.0,
        "warning_threshold": 10.0,
        "description": "Medication administration errors and near misses per 1,000 doses administered.",
    },
    {
        "indicator_code": "UNPLANNED_ICU_RETURN_RATE",
        "name": "Unplanned Return to ICU within 48 Hours",
        "standard": "NABH_5TH_ED",
        "unit": "PERCENT",
        "target_threshold": 2.0,
        "warning_threshold": 4.0,
        "description": "Percentage of patients stepped down from ICU returned unexpectedly within 48 hours.",
    },
    {
        "indicator_code": "UNPLANNED_OT_RETURN_RATE",
        "name": "Unplanned Return to Operating Theatre within 48 Hours",
        "standard": "NABH_5TH_ED",
        "unit": "PERCENT",
        "target_threshold": 1.0,
        "warning_threshold": 2.0,
        "description": "Percentage of surgical patients returned to OT for re-exploration within 48 hours.",
    },
]


def ensure_default_benchmarks(db: Session) -> None:
    """Idempotently seeds default NABH / JCI benchmarks if the table is empty."""
    count = db.scalar(select(func.count(QualityIndicatorBenchmark.id)))
    if count == 0:
        for b_data in DEFAULT_NABH_BENCHMARKS:
            db.add(QualityIndicatorBenchmark(**b_data))
        db.commit()


def calculate_quality_indicators(
    db: Session,
    start_at: datetime,
    end_at: datetime,
) -> Dict[str, Any]:
    """
    Computes standard hospital clinical quality indicators conforming to
    NABH 5th Edition and JCI 7th Edition accreditation requirements.
    """
    ensure_default_benchmarks(db)

    # 1. Denominators from operational clinical activity
    total_surgeries = db.scalar(
        select(func.count(SurgicalSchedule.id)).where(
            SurgicalSchedule.created_at >= start_at,
            SurgicalSchedule.created_at < end_at,
        )
    ) or 0

    total_doses = db.scalar(
        select(func.count(MedicationAdministrationLog.id)).where(
            MedicationAdministrationLog.administered_at >= start_at,
            MedicationAdministrationLog.administered_at < end_at,
        )
    ) or 0

    total_admissions = db.scalar(
        select(func.count(InpatientAdmission.id)).where(
            InpatientAdmission.created_at >= start_at,
            InpatientAdmission.created_at < end_at,
        )
    ) or 0

    # Inpatient bed days estimation (or floor based on admissions)
    estimated_bed_days = max(total_admissions * 3, 50)

    # 2. Numerators from clinical quality incidents table
    incident_rows = db.execute(
        select(
            ClinicalQualityIncident.incident_type,
            func.count(ClinicalQualityIncident.id),
            func.coalesce(func.sum(ClinicalQualityIncident.device_days), 0),
        )
        .where(
            ClinicalQualityIncident.reported_at >= start_at,
            ClinicalQualityIncident.reported_at < end_at,
        )
        .group_by(ClinicalQualityIncident.incident_type)
    ).all()

    incident_counts: Dict[str, int] = {}
    incident_device_days: Dict[str, int] = {}
    total_incidents = 0

    for itype, count, dev_days in incident_rows:
        type_val = itype.value if hasattr(itype, "value") else str(itype)
        incident_counts[type_val] = int(count)
        incident_device_days[type_val] = int(dev_days)
        total_incidents += int(count)

    # 3. Load benchmarks
    benchmarks_list = db.execute(
        select(QualityIndicatorBenchmark).where(QualityIndicatorBenchmark.is_active.is_(True))
    ).scalars().all()
    benchmarks = {b.indicator_code: b for b in benchmarks_list}

    def _eval_status(rate: float, target: float, warning: float) -> str:
        if rate <= target:
            return "COMPLIANT"
        elif rate <= warning:
            return "WARNING"
        return "NON_COMPLIANT"

    metrics: List[Dict[str, Any]] = []

    # CAUTI
    cauti_b = benchmarks.get("CAUTI_RATE")
    cauti_target = cauti_b.target_threshold if cauti_b else 2.0
    cauti_warn = cauti_b.warning_threshold if cauti_b else 3.5
    cauti_cases = incident_counts.get(ClinicalIncidentTypeEnum.CAUTI.value, 0)
    catheter_days = incident_device_days.get(ClinicalIncidentTypeEnum.CAUTI.value, 0) or max(estimated_bed_days // 4, 20)
    cauti_rate = round((cauti_cases / catheter_days) * 1000.0, 2)
    metrics.append({
        "code": "CAUTI_RATE",
        "name": "Catheter-Associated Urinary Tract Infection Rate",
        "numerator_cases": cauti_cases,
        "denominator_events": catheter_days,
        "rate": cauti_rate,
        "unit": "PER_1000_DEVICE_DAYS",
        "target_threshold": cauti_target,
        "warning_threshold": cauti_warn,
        "compliance_status": _eval_status(cauti_rate, cauti_target, cauti_warn),
    })

    # CLABSI
    clabsi_b = benchmarks.get("CLABSI_RATE")
    clabsi_target = clabsi_b.target_threshold if clabsi_b else 1.5
    clabsi_warn = clabsi_b.warning_threshold if clabsi_b else 3.0
    clabsi_cases = incident_counts.get(ClinicalIncidentTypeEnum.CLABSI.value, 0)
    central_line_days = incident_device_days.get(ClinicalIncidentTypeEnum.CLABSI.value, 0) or max(estimated_bed_days // 5, 20)
    clabsi_rate = round((clabsi_cases / central_line_days) * 1000.0, 2)
    metrics.append({
        "code": "CLABSI_RATE",
        "name": "Central Line-Associated Bloodstream Infection Rate",
        "numerator_cases": clabsi_cases,
        "denominator_events": central_line_days,
        "rate": clabsi_rate,
        "unit": "PER_1000_DEVICE_DAYS",
        "target_threshold": clabsi_target,
        "warning_threshold": clabsi_warn,
        "compliance_status": _eval_status(clabsi_rate, clabsi_target, clabsi_warn),
    })

    # VAP
    vap_b = benchmarks.get("VAP_RATE")
    vap_target = vap_b.target_threshold if vap_b else 2.0
    vap_warn = vap_b.warning_threshold if vap_b else 4.0
    vap_cases = incident_counts.get(ClinicalIncidentTypeEnum.VAP.value, 0)
    vent_days = incident_device_days.get(ClinicalIncidentTypeEnum.VAP.value, 0) or max(estimated_bed_days // 10, 15)
    vap_rate = round((vap_cases / vent_days) * 1000.0, 2)
    metrics.append({
        "code": "VAP_RATE",
        "name": "Ventilator-Associated Pneumonia Rate",
        "numerator_cases": vap_cases,
        "denominator_events": vent_days,
        "rate": vap_rate,
        "unit": "PER_1000_DEVICE_DAYS",
        "target_threshold": vap_target,
        "warning_threshold": vap_warn,
        "compliance_status": _eval_status(vap_rate, vap_target, vap_warn),
    })

    # SSI
    ssi_b = benchmarks.get("SSI_RATE")
    ssi_target = ssi_b.target_threshold if ssi_b else 1.5
    ssi_warn = ssi_b.warning_threshold if ssi_b else 2.5
    ssi_cases = incident_counts.get(ClinicalIncidentTypeEnum.SSI.value, 0)
    surgeries_denom = max(total_surgeries, 10)
    ssi_rate = round((ssi_cases / surgeries_denom) * 100.0, 2)
    metrics.append({
        "code": "SSI_RATE",
        "name": "Surgical Site Infection Rate",
        "numerator_cases": ssi_cases,
        "denominator_events": surgeries_denom,
        "rate": ssi_rate,
        "unit": "PERCENT",
        "target_threshold": ssi_target,
        "warning_threshold": ssi_warn,
        "compliance_status": _eval_status(ssi_rate, ssi_target, ssi_warn),
    })

    # Inpatient Fall Rate
    fall_b = benchmarks.get("FALL_RATE")
    fall_target = fall_b.target_threshold if fall_b else 1.5
    fall_warn = fall_b.warning_threshold if fall_b else 3.0
    fall_cases = incident_counts.get(ClinicalIncidentTypeEnum.PATIENT_FALL.value, 0)
    fall_rate = round((fall_cases / estimated_bed_days) * 1000.0, 2)
    metrics.append({
        "code": "FALL_RATE",
        "name": "Inpatient Fall Rate",
        "numerator_cases": fall_cases,
        "denominator_events": estimated_bed_days,
        "rate": fall_rate,
        "unit": "PER_1000_BED_DAYS",
        "target_threshold": fall_target,
        "warning_threshold": fall_warn,
        "compliance_status": _eval_status(fall_rate, fall_target, fall_warn),
    })

    # Medication Administration Error Rate
    med_b = benchmarks.get("MED_ERROR_RATE")
    med_target = med_b.target_threshold if med_b else 5.0
    med_warn = med_b.warning_threshold if med_b else 10.0
    med_cases = incident_counts.get(ClinicalIncidentTypeEnum.MEDICATION_ERROR.value, 0)
    doses_denom = max(total_doses, 50)
    med_rate = round((med_cases / doses_denom) * 1000.0, 2)
    metrics.append({
        "code": "MED_ERROR_RATE",
        "name": "Medication Administration Error Rate",
        "numerator_cases": med_cases,
        "denominator_events": doses_denom,
        "rate": med_rate,
        "unit": "PER_1000_DOSES",
        "target_threshold": med_target,
        "warning_threshold": med_warn,
        "compliance_status": _eval_status(med_rate, med_target, med_warn),
    })

    # Unplanned Return to ICU within 48h
    icu_b = benchmarks.get("UNPLANNED_ICU_RETURN_RATE")
    icu_target = icu_b.target_threshold if icu_b else 2.0
    icu_warn = icu_b.warning_threshold if icu_b else 4.0
    icu_cases = incident_counts.get(ClinicalIncidentTypeEnum.UNPLANNED_ICU_READMISSION.value, 0)
    admissions_denom = max(total_admissions, 10)
    icu_rate = round((icu_cases / admissions_denom) * 100.0, 2)
    metrics.append({
        "code": "UNPLANNED_ICU_RETURN_RATE",
        "name": "Unplanned Return to ICU within 48 Hours",
        "numerator_cases": icu_cases,
        "denominator_events": admissions_denom,
        "rate": icu_rate,
        "unit": "PERCENT",
        "target_threshold": icu_target,
        "warning_threshold": icu_warn,
        "compliance_status": _eval_status(icu_rate, icu_target, icu_warn),
    })

    # Unplanned Return to OT within 48h
    ot_b = benchmarks.get("UNPLANNED_OT_RETURN_RATE")
    ot_target = ot_b.target_threshold if ot_b else 1.0
    ot_warn = ot_b.warning_threshold if ot_b else 2.0
    ot_cases = incident_counts.get(ClinicalIncidentTypeEnum.UNPLANNED_OT_RETURN.value, 0)
    ot_rate = round((ot_cases / surgeries_denom) * 100.0, 2)
    metrics.append({
        "code": "UNPLANNED_OT_RETURN_RATE",
        "name": "Unplanned Return to Operating Theatre within 48 Hours",
        "numerator_cases": ot_cases,
        "denominator_events": surgeries_denom,
        "rate": ot_rate,
        "unit": "PERCENT",
        "target_threshold": ot_target,
        "warning_threshold": ot_warn,
        "compliance_status": _eval_status(ot_rate, ot_target, ot_warn),
    })

    return {
        "start_date": start_at,
        "end_date": end_at,
        "hospital_inpatient_bed_days": estimated_bed_days,
        "total_surgeries_performed": total_surgeries,
        "total_medication_doses_logged": total_doses,
        "total_incidents_recorded": total_incidents,
        "indicators": metrics,
    }
