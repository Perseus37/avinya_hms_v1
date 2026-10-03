from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db, resolve_tenant_context
from dependencies import RequirePermission
from models import (
    Appointment,
    ClinicalNote,
    DiagnosticOrder,
    EncounterDiagnosis,
    EncounterVital,
    InpatientAdmission,
    Invoice,
    PageSizeEnum,
    PatientAllergy,
    Prescription,
    ReportTemplate,
    ReportTypeEnum,
    TaskStatusEnum,
    User,
    InsuranceClaim,
    RadiologyStudyMetadata,
)
from services.standard_templates import (
    STANDARD_TEMPLATES,
    VARIABLE_CATALOG,
    render_sandboxed_template,
)

router = APIRouter(tags=["Printable Clinical & Financial Reports & Template Designer"])

IST = ZoneInfo("Asia/Kolkata")


def _fmt_ist(dt: Optional[datetime]) -> str:
    if not dt:
        return "N/A"
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M IST")
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def _fmt_inr(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def _get_patient_allergy_banner(db: Session, patient_id: int) -> str:
    alg_stmt = (
        select(PatientAllergy)
        .options(joinedload(PatientAllergy.allergy))
        .where(PatientAllergy.patient_id == patient_id, PatientAllergy.is_active == True)
    )
    allergies = list(db.execute(alg_stmt).scalars().all())
    if allergies:
        return ", ".join(f"{a.allergy.name} ({a.severity.value})" for a in allergies)
    return "No Known Drug Allergies (NKDA)"


def _resolve_template_for_print(
    db: Session,
    report_type: ReportTypeEnum,
    template_code: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """
    Resolves the requested template by code, or the tenant's active default template for report_type,
    falling back to the built-in STANDARD_TEMPLATES library if the DB table has no match.
    """
    if template_code:
        tpl = db.execute(
            select(ReportTemplate).where(
                ReportTemplate.code == template_code,
                ReportTemplate.report_type == report_type,
                ReportTemplate.is_active == True,
            )
        ).scalar_one_or_none()
        if not tpl:
            raise HTTPException(
                status_code=404,
                detail=f"Active template '{template_code}' for {report_type.value} not found.",
            )
        return {
            "html_template": tpl.html_template,
            "css_styles": tpl.css_styles,
            "header_html": tpl.header_html,
            "footer_html": tpl.footer_html,
        }

    default_tpl = db.execute(
        select(ReportTemplate)
        .where(
            ReportTemplate.report_type == report_type,
            ReportTemplate.is_default == True,
            ReportTemplate.is_active == True,
        )
        .order_by(ReportTemplate.id.desc())
    ).scalars().first()

    if default_tpl:
        return {
            "html_template": default_tpl.html_template,
            "css_styles": default_tpl.css_styles,
            "header_html": default_tpl.header_html,
            "footer_html": default_tpl.footer_html,
        }

    # Built-in zero-config fallback
    fallback = next(
        (t for t in STANDARD_TEMPLATES if t["report_type"] == report_type and t["is_default"]),
        STANDARD_TEMPLATES[0],
    )
    return {
        "html_template": fallback["html_template"],
        "css_styles": fallback["css_styles"],
        "header_html": fallback["header_html"],
        "footer_html": fallback["footer_html"],
    }


# =====================================================================
# SCHEMAS FOR VISUAL DRAG-AND-DROP TEMPLATE DESIGNER
# =====================================================================

class ReportTemplateCreate(BaseModel):
    report_type: ReportTypeEnum
    code: str = Field(min_length=3, max_length=50, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=3, max_length=100)
    description: Optional[str] = Field(default=None, max_length=255)
    page_size: PageSizeEnum = PageSizeEnum.A4_PORTRAIT
    layout_schema: Dict[str, Any]
    html_template: str = Field(min_length=5)
    css_styles: Optional[str] = ""
    header_html: Optional[str] = ""
    footer_html: Optional[str] = ""
    is_default: bool = False


class ReportTemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=3, max_length=100)
    description: Optional[str] = Field(default=None, max_length=255)
    page_size: Optional[PageSizeEnum] = None
    layout_schema: Optional[Dict[str, Any]] = None
    html_template: Optional[str] = None
    css_styles: Optional[str] = None
    header_html: Optional[str] = None
    footer_html: Optional[str] = None
    is_active: Optional[bool] = None


class ReportTemplateForkRequest(BaseModel):
    new_code: str = Field(min_length=3, max_length=50, pattern=r"^[A-Z0-9_]+$")
    new_name: str = Field(min_length=3, max_length=100)
    set_as_default: bool = False


class ReportTemplatePreviewRequest(BaseModel):
    report_type: ReportTypeEnum
    html_template: str
    css_styles: Optional[str] = ""
    header_html: Optional[str] = ""
    footer_html: Optional[str] = ""


class ReportTemplateResponse(BaseModel):
    id: int
    report_type: ReportTypeEnum
    code: str
    name: str
    description: Optional[str] = None
    page_size: PageSizeEnum
    layout_schema: Dict[str, Any]
    html_template: str
    css_styles: Optional[str] = None
    header_html: Optional[str] = None
    footer_html: Optional[str] = None
    is_default: bool
    is_system_template: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


def _serialize_template(tpl: ReportTemplate) -> Dict[str, Any]:
    try:
        parsed_layout = json.loads(tpl.layout_schema) if tpl.layout_schema else {}
    except Exception:
        parsed_layout = {}
    return {
        "id": tpl.id,
        "report_type": tpl.report_type,
        "code": tpl.code,
        "name": tpl.name,
        "description": tpl.description,
        "page_size": tpl.page_size,
        "layout_schema": parsed_layout,
        "html_template": tpl.html_template,
        "css_styles": tpl.css_styles,
        "header_html": tpl.header_html,
        "footer_html": tpl.footer_html,
        "is_default": tpl.is_default,
        "is_system_template": tpl.is_system_template,
        "is_active": tpl.is_active,
        "created_at": tpl.created_at,
        "updated_at": tpl.updated_at,
    }


# =====================================================================
# TEMPLATE DESIGNER CRUD, FORK, VARIABLE CATALOG & SANDBOX PREVIEW
# =====================================================================

@router.get("/api/reports/templates/variables/{report_type}")
def get_draggable_template_variables(
    report_type: ReportTypeEnum,
    _: User = Depends(RequirePermission("mdm:read", "opd:read", "billing:read")),
):
    """Returns all drag-and-drop variable tokens and table iterators for the visual template builder."""
    return {
        "report_type": report_type.value,
        "groups": VARIABLE_CATALOG.get(report_type, []),
    }


@router.get("/api/reports/templates", response_model=List[ReportTemplateResponse])
def list_report_templates(
    report_type: Optional[ReportTypeEnum] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read", "opd:read", "billing:read")),
):
    stmt = select(ReportTemplate).where(ReportTemplate.is_active == True)
    if report_type:
        stmt = stmt.where(ReportTemplate.report_type == report_type)
    stmt = stmt.order_by(ReportTemplate.report_type.asc(), ReportTemplate.id.asc())
    templates = db.execute(stmt).scalars().all()
    return [_serialize_template(t) for t in templates]

def _build_sample_context(
    hospital_name: str = "Avinya Hospital",
    branch_code: str = "HQ-HYD-01",
) -> Dict[str, Any]:
    return {
        "tenant": {
            "hospital_name": hospital_name,
            "branch_code": branch_code,
            "printed_at": _fmt_ist(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": "Vikram Rao",
            "mrn": "MRN-SAMPLE01",
            "gender": "MALE",
            "date_of_birth": "1988-04-12",
        },
        "doctor": {
            "full_name": "Dr. Gregory House",
            "qualification": "MD Internal Medicine",
            "specialization": "General Medicine",
            "nmc_reg": "NMC-2026-001",
        },
        "department": {"name": "General Medicine"},
        "appointment": {
            "id": 101,
            "appointment_time": "2026-10-02 15:00 IST",
            "status": "ARRIVED",
            "reason_for_visit": "High-grade fever and chills",
        },
        "vitals": {
            "weight_kg": "72.0 kg",
            "height_cm": "180.0 cm",
            "bmi": "22.22 kg/m²",
            "bp": "120/80 mmHg",
            "pulse_bpm": "76 bpm",
            "spo2_percent": "99%",
            "temperature_f": "98.6 °F",
            "respiratory_rate": "16 /min",
        },
        "allergy_banner": "Penicillin / Beta-Lactams (ANAPHYLACTIC)",
        "invoice": {
            "invoice_number": "INV-SAMPLE-001",
            "status": "PAID",
            "created_at": "2026-10-02 14:30 IST",
            "total_inr": "₹1,000.00",
            "discount_paise": 10000,
            "discount_inr": "₹100.00",
            "discount_reason": "Senior Citizen Concession",
            "net_inr": "₹900.00",
            "paid_inr": "₹900.00",
            "balance_inr": "₹0.00",
        },
        "insurance_claim": {
            "claim_number": "CLM-SAMPLE-01",
            "status": "SETTLED",
            "insurer_payable_inr": "₹720.00",
            "patient_copay_inr": "₹180.00",
            "settlement_utr_reference": "NEFT-SAMPLE-UTR",
        },
        "line_items": [
            {"description": "OPD Consultation", "quantity": 1, "unit_price_inr": "₹1,000.00", "total_price_inr": "₹1,000.00"}
        ],
        "payments": [
            {"receipt_number": "RCP-SAMPLE-01", "amount_inr": "₹900.00", "payment_method": "UPI", "transaction_reference": "UPI-123"}
        ],
        "prescription": {"id": 1, "clinical_notes": "Drink plenty of warm fluids."},
        "medications": [
            {"brand_name": "Dolo-650", "generic_name": "Paracetamol", "strength": "650mg", "dosage_instruction": "1-1-1", "requested_quantity": 10}
        ],
        "order": {"id": 1, "ordering_doctor_name": "Dr. Gregory House", "clinical_notes": "Rule out viral fever"},
        "tasks": [
            {
                "service_code": "LAB_HB",
                "service_name": "Hemoglobin",
                "result_value": "14.2 g/dL",
                "result_notes": "Normal",
                "performed_by_name": "Tech A",
                "verified_by_name": "Dr. Pathologist",
                "dicom_modality": "N/A",
                "dicom_accession_number": "N/A",
                "pacs_viewer_url": "",
                "radiologist_impression": "",
            }
        ],
        "admission": {
            "admitting_doctor_name": "Dr. Gregory House",
            "discharge_time": "2026-10-02 14:00 IST",
            "discharge_disposition": "ROUTINE_DISCHARGE",
            "discharge_summary": "Recovered fully. Review in OPD after 7 days.",
        },
        "diagnoses": [{"diagnosis_type": "PRIMARY", "icd10_code": "J18.9", "short_description": "Pneumonia, unspecified"}],
        "signed_notes": [{"assessment_notes": "Community acquired pneumonia", "plan_notes": "IV antibiotics completed"}],
        "mlc": {
            "mlc_number": "MLC-SAMPLE-01",
            "cause_of_injury": "ROAD_TRAFFIC_ACCIDENT",
            "alleged_history": "Two-wheeler skid",
            "identification_marks": "Mole on right cheek",
            "brought_by_name": "Ramesh Kumar",
            "brought_by_relation": "Brother",
            "police_station_name": "Madhapur PS",
            "police_constable_buckle_no": "PC-4421",
            "fir_number": "FIR-2026-01",
            "police_intimated_at": "2026-10-02 20:00 IST",
            "doctor_name": "Dr. Gregory House",
        },
        "surgery": {
            "case_number": "OT-SAMPLE-01",
            "theatre_name": "Modular Theatre 1",
            "procedure_name": "Total Hip Arthroplasty",
            "surgeon_name": "Dr. Gregory House",
        },
        "record": {
            "anesthesia_type": "SPINAL",
            "asa_physical_status": 2,
            "estimated_blood_loss_ml": 250,
            "operative_findings": "Prosthesis seated cleanly.",
            "post_op_instructions": "Monitor vitals q1h.",
        },
        "implants": [
            {"implant_name": "Titanium Stem", "manufacturer": "Zimmer", "serial_or_batch_number": "ZIM-99", "quantity": 1}
        ],
        "mother": {"full_name": "Ananya Rao", "mrn": "MRN-MOM01"},
        "birth": {
            "certificate_number": "BIRTH-SAMPLE-01",
            "baby_full_name": "Baby of Ananya Rao",
            "newborn_mrn": "MRN-NB-SAMPLE",
            "baby_gender": "FEMALE",
            "time_of_birth": "2026-10-03 08:30 IST",
            "delivery_mode": "NORMAL_VAGINAL",
            "birth_weight_kg": 3.2,
            "apgar_1min": 8,
            "apgar_5min": 9,
            "doctor_name": "Dr. Gregory House",
        },
        "death": {
            "certificate_number": "MCCD-SAMPLE-01",
            "time_of_death": "2026-10-03 04:15 IST",
            "manner_of_death": "NATURAL",
            "icd10_code": "I21.9",
            "diagnosis_description": "Acute myocardial infarction",
            "antecedent_cause_notes": "Coronary artery disease",
            "is_autopsy_advised": "No",
            "body_handed_over_to": "Next of Kin",
            "doctor_name": "Dr. Gregory House",
        },
    }

@router.post(
    "/api/reports/templates",
    response_model=ReportTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_custom_report_template(
    payload: ReportTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    existing = db.execute(select(ReportTemplate).where(ReportTemplate.code == payload.code)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Template code '{payload.code}' already exists.")

    # Validate template syntax & sandbox security before saving
    render_sandboxed_template(
        payload.html_template, payload.css_styles, payload.header_html, payload.footer_html, _build_sample_context()
    )

    if payload.is_default:
        prev_defaults = db.execute(
            select(ReportTemplate).where(
                ReportTemplate.report_type == payload.report_type,
                ReportTemplate.is_default == True,
            )
        ).scalars().all()
        for pt in prev_defaults:
            pt.is_default = False

    tpl = ReportTemplate(
        report_type=payload.report_type,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        page_size=payload.page_size,
        layout_schema=json.dumps(payload.layout_schema),
        html_template=payload.html_template,
        css_styles=payload.css_styles,
        header_html=payload.header_html,
        footer_html=payload.footer_html,
        is_default=payload.is_default,
        is_system_template=False,
        is_active=True,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return _serialize_template(tpl)


@router.post(
    "/api/reports/templates/fork/{source_code}",
    response_model=ReportTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def fork_existing_template(
    source_code: str,
    payload: ReportTemplateForkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    """Imports/forks a standard or existing template into a customizable hospital template."""
    source_tpl = db.execute(select(ReportTemplate).where(ReportTemplate.code == source_code)).scalar_one_or_none()
    if not source_tpl:
        raise HTTPException(status_code=404, detail=f"Source template '{source_code}' not found.")

    dup = db.execute(select(ReportTemplate).where(ReportTemplate.code == payload.new_code)).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"Template code '{payload.new_code}' already exists.")

    if payload.set_as_default:
        prev_defaults = db.execute(
            select(ReportTemplate).where(
                ReportTemplate.report_type == source_tpl.report_type,
                ReportTemplate.is_default == True,
            )
        ).scalars().all()
        for pt in prev_defaults:
            pt.is_default = False

    forked = ReportTemplate(
        report_type=source_tpl.report_type,
        code=payload.new_code,
        name=payload.new_name,
        description=f"Forked from {source_tpl.code}",
        page_size=source_tpl.page_size,
        layout_schema=source_tpl.layout_schema,
        html_template=source_tpl.html_template,
        css_styles=source_tpl.css_styles,
        header_html=source_tpl.header_html,
        footer_html=source_tpl.footer_html,
        is_default=payload.set_as_default,
        is_system_template=False,
        is_active=True,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    db.add(forked)
    db.commit()
    db.refresh(forked)
    return _serialize_template(forked)


@router.put("/api/reports/templates/{template_id}", response_model=ReportTemplateResponse)
def update_custom_report_template(
    template_id: int,
    payload: ReportTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    """Updates widget positions in layout_schema, HTML, or CSS of a hospital's custom template."""
    tpl = db.get(ReportTemplate, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found.")
    if tpl.is_system_template:
        raise HTTPException(
            status_code=400,
            detail="System standard templates are read-only. Fork the template first to customize it.",
        )

    new_html = payload.html_template if payload.html_template is not None else tpl.html_template
    new_css = payload.css_styles if payload.css_styles is not None else tpl.css_styles
    new_hdr = payload.header_html if payload.header_html is not None else tpl.header_html
    new_ftr = payload.footer_html if payload.footer_html is not None else tpl.footer_html
    render_sandboxed_template(new_html, new_css, new_hdr, new_ftr, _build_sample_context())

    if payload.name is not None:
        tpl.name = payload.name
    if payload.description is not None:
        tpl.description = payload.description
    if payload.page_size is not None:
        tpl.page_size = payload.page_size
    if payload.layout_schema is not None:
        tpl.layout_schema = json.dumps(payload.layout_schema)
    if payload.html_template is not None:
        tpl.html_template = payload.html_template
    if payload.css_styles is not None:
        tpl.css_styles = payload.css_styles
    if payload.header_html is not None:
        tpl.header_html = payload.header_html
    if payload.footer_html is not None:
        tpl.footer_html = payload.footer_html
    if payload.is_active is not None:
        tpl.is_active = payload.is_active

    tpl.updated_by_id = current_user.id
    db.commit()
    db.refresh(tpl)
    return _serialize_template(tpl)


@router.patch("/api/reports/templates/{template_id}/set-default", response_model=ReportTemplateResponse)
def set_default_report_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    """Promotes a template (system or custom) to be the hospital's active default for its report_type."""
    tpl = db.get(ReportTemplate, template_id)
    if not tpl or not tpl.is_active:
        raise HTTPException(status_code=404, detail="Active template not found.")

    prev_defaults = db.execute(
        select(ReportTemplate).where(
            ReportTemplate.report_type == tpl.report_type,
            ReportTemplate.is_default == True,
        )
    ).scalars().all()
    for pt in prev_defaults:
        pt.is_default = False

    tpl.is_default = True
    tpl.updated_by_id = current_user.id
    db.commit()
    db.refresh(tpl)
    return _serialize_template(tpl)


@router.post("/api/reports/templates/preview", response_class=HTMLResponse)
def preview_report_template(
    payload: ReportTemplatePreviewRequest,
    request: Request,
    _: User = Depends(RequirePermission("mdm:write")),
):
    """Renders a live preview of a draft template inside the sandbox using realistic sample data."""
    tenant = resolve_tenant_context(request)
    html = render_sandboxed_template(
        payload.html_template,
        payload.css_styles,
        payload.header_html,
        payload.footer_html,
        _build_sample_context(tenant.hospital_name, tenant.branch_code),
    )
    return HTMLResponse(content=html, status_code=200)


# =====================================================================
# 5 LIVE PRINT ENDPOINTS (WITH DYNAMIC ?template_code= SWITCHING)
# =====================================================================

# 1. OPD Appointment & Consultation Case Sheet (with BMI/Vitals & Blank Doctor Writing Space)
@router.get("/api/opd/appointments/{appointment_id}/print", response_class=HTMLResponse)
def print_opd_appointment_case_sheet(
    appointment_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read", "adt:read")),
):
    tenant = resolve_tenant_context(request)
    stmt = (
        select(Appointment)
        .options(
            joinedload(Appointment.patient),
            joinedload(Appointment.doctor).joinedload(User.doctor_profile),
            joinedload(Appointment.department),
        )
        .where(Appointment.id == appointment_id)
    )
    appt = db.execute(stmt).unique().scalar_one_or_none()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    patient = appt.patient
    doc = appt.doctor
    doc_profile = doc.doctor_profile

    # Fetch latest EncounterVital if an encounter is linked (or latest active encounter for patient)
    latest_vital: Optional[EncounterVital] = None
    if appt.encounter_id:
        latest_vital = (
            db.execute(
                select(EncounterVital)
                .where(EncounterVital.encounter_id == appt.encounter_id)
                .order_by(EncounterVital.id.desc())
            )
            .scalars()
            .first()
        )

    vitals_ctx = {
        "weight_kg": f"{latest_vital.weight_kg} kg" if (latest_vital and latest_vital.weight_kg) else "____ kg",
        "height_cm": f"{latest_vital.height_cm} cm" if (latest_vital and latest_vital.height_cm) else "____ cm",
        "bmi": f"{latest_vital.bmi} kg/m²" if (latest_vital and latest_vital.bmi) else "____ kg/m²",
        "bp": (
            f"{latest_vital.bp_systolic}/{latest_vital.bp_diastolic} mmHg"
            if (latest_vital and latest_vital.bp_systolic and latest_vital.bp_diastolic)
            else "___/___ mmHg"
        ),
        "pulse_bpm": f"{latest_vital.pulse_bpm} bpm" if (latest_vital and latest_vital.pulse_bpm) else "____ bpm",
        "spo2_percent": f"{latest_vital.spo2_percent}%" if (latest_vital and latest_vital.spo2_percent) else "____%",
        "temperature_f": f"{latest_vital.temperature_f} °F" if (latest_vital and latest_vital.temperature_f) else "____ °F",
        "respiratory_rate": f"{latest_vital.respiratory_rate} /min" if (latest_vital and latest_vital.respiratory_rate) else "____ /min",
    }

    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_ist(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": patient.full_name,
            "mrn": patient.mrn,
            "gender": patient.gender.value,
            "date_of_birth": patient.date_of_birth.strftime("%Y-%m-%d"),
        },
        "doctor": {
            "full_name": doc.full_name,
            "qualification": doc_profile.qualification if doc_profile else "MD",
            "specialization": doc_profile.specialization if doc_profile else "General",
            "nmc_reg": doc_profile.medical_registration_number if doc_profile else "N/A",
        },
        "department": {"name": appt.department.name if appt.department else "OPD"},
        "appointment": {
            "id": appt.id,
            "appointment_time": _fmt_ist(appt.appointment_time),
            "status": appt.status.value,
            "reason_for_visit": appt.reason_for_visit or "General Consultation",
        },
        "vitals": vitals_ctx,
        "allergy_banner": _get_patient_allergy_banner(db, patient.id),
    }

    tpl_parts = _resolve_template_for_print(db, ReportTypeEnum.OPD_APPOINTMENT_SLIP, template_code)
    html = render_sandboxed_template(
        tpl_parts["html_template"],
        tpl_parts["css_styles"],
        tpl_parts["header_html"],
        tpl_parts["footer_html"],
        context,
    )
    return HTMLResponse(content=html, status_code=200)


# 2. Printable Tax Invoice & Payment Receipt
@router.get("/api/billing/invoices/{invoice_id}/print", response_class=HTMLResponse)
def print_invoice_and_receipt(
    invoice_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read")),
):
    tenant = resolve_tenant_context(request)
    stmt = (
        select(Invoice)
        .options(
            joinedload(Invoice.patient),
            joinedload(Invoice.line_items),
            joinedload(Invoice.payments),
        )
        .where(Invoice.id == invoice_id)
    )
    invoice = db.execute(stmt).unique().scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    balance_paise = invoice.net_amount_paise - invoice.amount_paid_paise
    claim = db.execute(select(InsuranceClaim).where(InsuranceClaim.invoice_id == invoice.id)).scalar_one_or_none()

    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_ist(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": invoice.patient.full_name,
            "mrn": invoice.patient.mrn,
            "gender": invoice.patient.gender.value,
            "date_of_birth": invoice.patient.date_of_birth.strftime("%Y-%m-%d"),
        },
        "invoice": {
            "invoice_number": invoice.invoice_number,
            "status": invoice.status.value,
            "created_at": _fmt_ist(invoice.created_at),
            "total_inr": _fmt_inr(invoice.total_amount_paise),
            "discount_paise": invoice.discount_amount_paise or 0,
            "discount_inr": _fmt_inr(invoice.discount_amount_paise or 0),
            "discount_reason": invoice.discount_reason or "None",
            "net_inr": _fmt_inr(invoice.net_amount_paise),
            "paid_inr": _fmt_inr(invoice.amount_paid_paise),
            "balance_inr": _fmt_inr(balance_paise),
        },
        "insurance_claim": {
            "claim_number": claim.claim_number if claim else "N/A",
            "status": claim.status.value if claim else "N/A",
            "insurer_payable_inr": _fmt_inr(claim.insurer_payable_paise) if claim else "₹0.00",
            "patient_copay_inr": _fmt_inr(claim.patient_copay_paise) if claim else "₹0.00",
            "settlement_utr_reference": (claim.settlement_utr_reference or "Pending") if claim else "N/A",
        },
        "line_items": [
            {
                "description": item.description,
                "quantity": item.quantity,
                "unit_price_inr": _fmt_inr(item.unit_price_paise),
                "total_price_inr": _fmt_inr(item.total_price_paise),
            }
            for item in invoice.line_items
        ],
        "payments": [
            {
                "receipt_number": p.receipt_number,
                "amount_inr": _fmt_inr(p.amount_paid_paise),
                "payment_method": p.payment_method.value,
                "transaction_reference": p.transaction_reference or "N/A",
            }
            for p in invoice.payments
        ],
    }

    tpl_parts = _resolve_template_for_print(db, ReportTypeEnum.INVOICE, template_code)
    html = render_sandboxed_template(
        tpl_parts["html_template"],
        tpl_parts["css_styles"],
        tpl_parts["header_html"],
        tpl_parts["footer_html"],
        context,
    )
    return HTMLResponse(content=html, status_code=200)


# 3. Printable eRx Medical Prescription
@router.get("/api/pharmacy/prescriptions/{prescription_id}/print", response_class=HTMLResponse)
def print_erx_prescription(
    prescription_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:read", "opd:read", "adt:read")),
):
    tenant = resolve_tenant_context(request)
    stmt = (
        select(Prescription)
        .options(
            joinedload(Prescription.encounter).joinedload(
                Prescription.encounter.property.mapper.class_.patient
            ),
            joinedload(Prescription.prescribing_doctor).joinedload(User.doctor_profile),
            joinedload(Prescription.items).joinedload(
                Prescription.items.property.mapper.class_.medication
            ),
        )
        .where(Prescription.id == prescription_id)
    )
    rx = db.execute(stmt).unique().scalar_one_or_none()
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found.")

    patient = rx.encounter.patient
    doc = rx.prescribing_doctor
    nmc_reg = doc.doctor_profile.medical_registration_number if doc.doctor_profile else "N/A"

    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_ist(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": patient.full_name,
            "mrn": patient.mrn,
            "gender": patient.gender.value,
            "date_of_birth": patient.date_of_birth.strftime("%Y-%m-%d"),
        },
        "doctor": {
            "full_name": doc.full_name,
            "nmc_reg": nmc_reg,
        },
        "prescription": {
            "id": rx.id,
            "clinical_notes": rx.clinical_notes or "None",
        },
        "allergy_banner": _get_patient_allergy_banner(db, patient.id),
        "medications": [
            {
                "brand_name": item.medication.brand_name,
                "generic_name": item.medication.generic_name,
                "strength": item.medication.strength,
                "dosage_instruction": item.dosage_instruction,
                "requested_quantity": item.requested_quantity,
            }
            for item in rx.items
        ],
    }

    tpl_parts = _resolve_template_for_print(db, ReportTypeEnum.PRESCRIPTION, template_code)
    html = render_sandboxed_template(
        tpl_parts["html_template"],
        tpl_parts["css_styles"],
        tpl_parts["header_html"],
        tpl_parts["footer_html"],
        context,
    )
    return HTMLResponse(content=html, status_code=200)


# 4. Printable Verified Diagnostic Lab & Radiology Report
@router.get("/api/diagnostics/orders/{order_id}/print", response_class=HTMLResponse)
def print_diagnostic_lab_report(
    order_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:read")),
):
    tenant = resolve_tenant_context(request)
    stmt = (
        select(DiagnosticOrder)
        .options(
            joinedload(DiagnosticOrder.patient),
            joinedload(DiagnosticOrder.ordering_doctor),
            joinedload(DiagnosticOrder.tasks).joinedload(
                DiagnosticOrder.tasks.property.mapper.class_.service
            ),
            joinedload(DiagnosticOrder.tasks).joinedload(
                DiagnosticOrder.tasks.property.mapper.class_.performed_by
            ),
            joinedload(DiagnosticOrder.tasks).joinedload(
                DiagnosticOrder.tasks.property.mapper.class_.verified_by
            ),
        )
        .where(DiagnosticOrder.id == order_id)
    )
    order = db.execute(stmt).unique().scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Diagnostic order not found.")

    unverified = [t for t in order.tasks if t.status != TaskStatusEnum.VERIFIED]
    if unverified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot print official lab report: one or more diagnostic tasks are not yet VERIFIED.",
        )

    task_ids = [t.id for t in order.tasks]
    studies_map = {}
    if task_ids:
        studies = db.execute(
            select(RadiologyStudyMetadata).where(RadiologyStudyMetadata.diagnostic_task_id.in_(task_ids))
        ).scalars().all()
        studies_map = {s.diagnostic_task_id: s for s in studies}
        
    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_ist(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": order.patient.full_name,
            "mrn": order.patient.mrn,
            "gender": order.patient.gender.value,
            "date_of_birth": order.patient.date_of_birth.strftime("%Y-%m-%d"),
        },
        "order": {
            "id": order.id,
            "ordering_doctor_name": order.ordering_doctor.full_name,
            "clinical_notes": order.clinical_notes or "None",
        },
        "tasks": [
            {
                "service_code": t.service.code,
                "service_name": t.service.name,
                "result_value": t.result_value,
                "result_notes": t.result_notes or "",
                "performed_by_name": t.performed_by.full_name if t.performed_by else "N/A",
                "verified_by_name": t.verified_by.full_name if t.verified_by else "N/A",
                "dicom_modality": studies_map[t.id].modality.value if t.id in studies_map else "N/A",
                "dicom_accession_number": studies_map[t.id].accession_number if t.id in studies_map else "N/A",
                "pacs_viewer_url": studies_map[t.id].pacs_viewer_url if t.id in studies_map else "",
                "radiologist_impression": (studies_map[t.id].radiologist_impression or "") if t.id in studies_map else "",
            }
            for t in order.tasks
        ],
    }

    tpl_parts = _resolve_template_for_print(db, ReportTypeEnum.DIAGNOSTIC_REPORT, template_code)
    html = render_sandboxed_template(
        tpl_parts["html_template"],
        tpl_parts["css_styles"],
        tpl_parts["header_html"],
        tpl_parts["footer_html"],
        context,
    )
    return HTMLResponse(content=html, status_code=200)


# 5. Printable IPD Clinical Discharge Summary
@router.get("/api/adt/admissions/{admission_id}/discharge-summary/print", response_class=HTMLResponse)
def print_ipd_discharge_summary(
    admission_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read")),
):
    tenant = resolve_tenant_context(request)
    stmt = (
        select(InpatientAdmission)
        .options(
            joinedload(InpatientAdmission.encounter).joinedload(
                InpatientAdmission.encounter.property.mapper.class_.patient
            ),
            joinedload(InpatientAdmission.admitting_doctor),
        )
        .where(InpatientAdmission.id == admission_id)
    )
    admission = db.execute(stmt).unique().scalar_one_or_none()
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found.")
    if admission.discharge_time is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot print discharge summary for an active admission that has not been discharged.",
        )

    enc = admission.encounter
    patient = enc.patient

    diag_stmt = (
        select(EncounterDiagnosis)
        .options(joinedload(EncounterDiagnosis.diagnosis))
        .where(EncounterDiagnosis.encounter_id == enc.id)
    )
    diagnoses = list(db.execute(diag_stmt).scalars().all())

    notes_stmt = select(ClinicalNote).where(
        ClinicalNote.encounter_id == enc.id,
        ClinicalNote.is_signed == True,
    )
    signed_notes = list(db.execute(notes_stmt).scalars().all())

    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_ist(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": patient.full_name,
            "mrn": patient.mrn,
            "gender": patient.gender.value,
            "date_of_birth": patient.date_of_birth.strftime("%Y-%m-%d"),
        },
        "admission": {
            "admitting_doctor_name": admission.admitting_doctor.full_name,
            "discharge_time": _fmt_ist(admission.discharge_time),
            "discharge_disposition": admission.discharge_disposition.value,
            "discharge_summary": admission.discharge_summary or "None",
        },
        "diagnoses": [
            {
                "diagnosis_type": d.diagnosis_type.value,
                "icd10_code": d.diagnosis.icd10_code,
                "short_description": d.diagnosis.short_description,
            }
            for d in diagnoses
        ],
        "signed_notes": [
            {
                "assessment_notes": n.assessment_notes or "",
                "plan_notes": n.plan_notes or "",
            }
            for n in signed_notes
        ],
    }

    tpl_parts = _resolve_template_for_print(db, ReportTypeEnum.DISCHARGE_SUMMARY, template_code)
    html = render_sandboxed_template(
        tpl_parts["html_template"],
        tpl_parts["css_styles"],
        tpl_parts["header_html"],
        tpl_parts["footer_html"],
        context,
    )
    return HTMLResponse(content=html, status_code=200)