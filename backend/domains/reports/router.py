from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from babel import Locale
from babel.numbers import format_currency, format_decimal, get_currency_symbol
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db, resolve_tenant_context
from backend.core.config import settings
from backend.core.dependencies import RequirePermission
from backend.domains.billing.models import Invoice
from backend.domains.bloodbank.models import RadiologyStudyMetadata
from backend.domains.clinical.models import ClinicalNote, EncounterDiagnosis, EncounterVital
from backend.domains.diagnostics.models import DiagnosticOrder, TaskStatusEnum
from backend.domains.encounters.models import Appointment, InpatientAdmission
from backend.domains.identity.models import User
from backend.domains.insurance.models import InsuranceClaim
from backend.domains.patients.models import PatientAllergy
from backend.domains.pharmacy.models import Prescription
from backend.domains.reports.models import (
    PageSizeEnum,
    PrintModeEnum,
    ReportTemplate,
    ReportTypeEnum,
)
from backend.domains.reports.templates import (
    STANDARD_TEMPLATES,
    VARIABLE_CATALOG,
    render_sandboxed_template,
)
from backend.domains.reports.pdf import render_html_pdf
from backend.domains.policies.models import HospitalPolicySetting
from backend.domains.reports.profiles import InvoiceDocumentProfile
from backend.domains.privacy.runtime import evaluate_tenant_patient_data_uses

router = APIRouter(tags=["Printable Clinical & Financial Reports & Template Designer"])

APP_TIMEZONE = ZoneInfo(settings.APP_TIMEZONE)


def _fmt_local_time(dt: Optional[datetime]) -> str:
    if not dt:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=APP_TIMEZONE)
    else:
        dt = dt.astimezone(APP_TIMEZONE)
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def _fmt_inr(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def _invoice_document_profile(db: Session) -> Dict[str, Any]:
    setting = db.execute(select(HospitalPolicySetting)).scalars().first()
    try:
        profile = json.loads(setting.invoice_document_profile_json or "{}") if setting else {}
    except (TypeError, json.JSONDecodeError):
        profile = {}
    if not isinstance(profile, dict):
        profile = {}
    try:
        return InvoiceDocumentProfile.model_validate(profile).model_dump()
    except ValidationError as exc:
        raise HTTPException(
            status_code=500,
            detail="The hospital invoice document profile is invalid; update its currency and tax registration settings.",
        ) from exc


def _format_invoice_amount(amount_minor: int, profile: Dict[str, Any]) -> str:
    digits = profile["minor_unit_digits"]
    amount = Decimal(int(amount_minor)) / (Decimal(10) ** digits)
    locale = profile["currency_locale"].replace("-", "_")
    symbol = profile.get("currency_symbol")
    locale_info = Locale.parse(locale)
    pattern = locale_info.currency_formats["standard"].pattern
    fraction = f".{'0' * digits}" if digits else ""
    pattern = re.sub(r"\.[#0]+", fraction, pattern)
    if symbol:
        number_format = locale_info.decimal_formats[None].pattern
        number_format = re.sub(r"\.[#0]+", fraction, number_format)
        return f"{symbol}{format_decimal(amount, format=number_format, locale=locale)}"
    return format_currency(
        amount,
        profile["currency_code"],
        locale=locale,
        currency_digits=False,
        format=pattern,
    )


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
    print_mode: Optional[PrintModeEnum] = None,
) -> Dict[str, Optional[str]]:
    """
    Resolves the requested template by code, or the tenant's active default template for report_type,
    falling back to the built-in STANDARD_TEMPLATES library if the DB table has no match.
    """
    setting = db.execute(select(HospitalPolicySetting)).scalars().first()
    try:
        configured_modes = json.loads(
            setting.report_print_modes_json or "{}"
        ) if setting else {}
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail="The hospital report print mode configuration is invalid.",
        ) from exc
    if not isinstance(configured_modes, dict):
        raise HTTPException(
            status_code=500,
            detail="The hospital report print mode configuration is invalid.",
        )
    configured_mode_value = configured_modes.get(
        report_type.value, PrintModeEnum.DESIGNED.value
    )
    try:
        configured_mode = PrintModeEnum(configured_mode_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"The configured print mode for {report_type.value} is invalid.",
        ) from exc
    requested_mode = print_mode or configured_mode

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
        if print_mode is not None and tpl.print_mode != print_mode.value:
            raise HTTPException(
                status_code=422,
                detail="The selected template does not match the requested print_mode.",
            )
        return {
            "html_template": tpl.html_template,
            "css_styles": tpl.css_styles,
            "header_html": tpl.header_html,
            "footer_html": tpl.footer_html,
            "page_size": tpl.page_size.value,
            "print_mode": tpl.print_mode,
            "paper_width_mm": tpl.paper_width_mm,
            "paper_height_mm": tpl.paper_height_mm,
            "page_margin_top_mm": tpl.page_margin_top_mm,
            "page_margin_right_mm": tpl.page_margin_right_mm,
            "page_margin_bottom_mm": tpl.page_margin_bottom_mm,
            "page_margin_left_mm": tpl.page_margin_left_mm,
            "content_offset_x_mm": tpl.content_offset_x_mm,
            "content_offset_y_mm": tpl.content_offset_y_mm,
        }

    default_tpl = db.execute(
        select(ReportTemplate)
        .where(
            ReportTemplate.report_type == report_type,
            ReportTemplate.print_mode == requested_mode.value,
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
            "page_size": default_tpl.page_size.value,
            "print_mode": default_tpl.print_mode,
            "paper_width_mm": default_tpl.paper_width_mm,
            "paper_height_mm": default_tpl.paper_height_mm,
            "page_margin_top_mm": default_tpl.page_margin_top_mm,
            "page_margin_right_mm": default_tpl.page_margin_right_mm,
            "page_margin_bottom_mm": default_tpl.page_margin_bottom_mm,
            "page_margin_left_mm": default_tpl.page_margin_left_mm,
            "content_offset_x_mm": default_tpl.content_offset_x_mm,
            "content_offset_y_mm": default_tpl.content_offset_y_mm,
        }

    if requested_mode != PrintModeEnum.DESIGNED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No active default {requested_mode.value} template is configured "
                f"for {report_type.value}."
            ),
        )

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
        "page_size": fallback["page_size"].value,
        "print_mode": PrintModeEnum.DESIGNED.value,
        "paper_width_mm": None,
        "paper_height_mm": None,
        "page_margin_top_mm": None,
        "page_margin_right_mm": None,
        "page_margin_bottom_mm": None,
        "page_margin_left_mm": None,
        "content_offset_x_mm": None,
        "content_offset_y_mm": None,
    }


def _render_template_parts(
    template_parts: Dict[str, Any], context: Dict[str, Any]
) -> str:
    render_context = dict(context)
    render_context["tenant"] = {
        **context.get("tenant", {}),
        "print_mode": template_parts["print_mode"],
        "page_size": template_parts["page_size"],
    }
    return render_sandboxed_template(
        template_parts["html_template"],
        template_parts["css_styles"],
        None
        if template_parts["print_mode"]
        == PrintModeEnum.PREPRINTED_DATA_ONLY.value
        else template_parts["header_html"],
        None
        if template_parts["print_mode"]
        == PrintModeEnum.PREPRINTED_DATA_ONLY.value
        else template_parts["footer_html"],
        render_context,
        page_size=template_parts["page_size"],
        print_mode=template_parts["print_mode"],
        paper_width_mm=template_parts["paper_width_mm"],
        paper_height_mm=template_parts["paper_height_mm"],
        page_margins_mm={
            "top": template_parts["page_margin_top_mm"],
            "right": template_parts["page_margin_right_mm"],
            "bottom": template_parts["page_margin_bottom_mm"],
            "left": template_parts["page_margin_left_mm"],
        },
        content_offset_x_mm=template_parts["content_offset_x_mm"],
        content_offset_y_mm=template_parts["content_offset_y_mm"],
    )


def _validate_printable_area(
    width: Optional[float],
    height: Optional[float],
    top: Optional[float],
    right: Optional[float],
    bottom: Optional[float],
    left: Optional[float],
) -> None:
    if width is None or height is None:
        return
    if (top or 0) + (bottom or 0) >= height:
        raise HTTPException(
            status_code=422,
            detail="Top and bottom margins must leave a positive printable height.",
        )
    if (left or 0) + (right or 0) >= width:
        raise HTTPException(
            status_code=422,
            detail="Left and right margins must leave a positive printable width.",
        )


# =====================================================================
# SCHEMAS FOR VISUAL DRAG-AND-DROP TEMPLATE DESIGNER
# =====================================================================

class ReportTemplateCreate(BaseModel):
    report_type: ReportTypeEnum
    print_mode: PrintModeEnum = PrintModeEnum.DESIGNED
    code: str = Field(min_length=3, max_length=50, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=3, max_length=100)
    description: Optional[str] = Field(default=None, max_length=255)
    page_size: PageSizeEnum = PageSizeEnum.A4_PORTRAIT
    paper_width_mm: Optional[float] = Field(default=None, ge=10, le=1000)
    paper_height_mm: Optional[float] = Field(default=None, ge=10, le=1000)
    page_margin_top_mm: Optional[float] = Field(default=None, ge=0, le=100)
    page_margin_right_mm: Optional[float] = Field(default=None, ge=0, le=100)
    page_margin_bottom_mm: Optional[float] = Field(default=None, ge=0, le=100)
    page_margin_left_mm: Optional[float] = Field(default=None, ge=0, le=100)
    content_offset_x_mm: Optional[float] = Field(default=None, ge=-100, le=100)
    content_offset_y_mm: Optional[float] = Field(default=None, ge=-100, le=100)
    layout_schema: Dict[str, Any]
    html_template: str = Field(min_length=5)
    css_styles: Optional[str] = ""
    header_html: Optional[str] = ""
    footer_html: Optional[str] = ""
    is_default: bool = False

    @model_validator(mode="after")
    def validate_paper_dimensions(self):
        if (self.paper_width_mm is None) != (self.paper_height_mm is None):
            raise ValueError("paper_width_mm and paper_height_mm must be set together.")
        return self


class ReportTemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=3, max_length=100)
    description: Optional[str] = Field(default=None, max_length=255)
    page_size: Optional[PageSizeEnum] = None
    paper_width_mm: Optional[float] = Field(default=None, ge=10, le=1000)
    paper_height_mm: Optional[float] = Field(default=None, ge=10, le=1000)
    page_margin_top_mm: Optional[float] = Field(default=None, ge=0, le=100)
    page_margin_right_mm: Optional[float] = Field(default=None, ge=0, le=100)
    page_margin_bottom_mm: Optional[float] = Field(default=None, ge=0, le=100)
    page_margin_left_mm: Optional[float] = Field(default=None, ge=0, le=100)
    content_offset_x_mm: Optional[float] = Field(default=None, ge=-100, le=100)
    content_offset_y_mm: Optional[float] = Field(default=None, ge=-100, le=100)
    layout_schema: Optional[Dict[str, Any]] = None
    html_template: Optional[str] = None
    css_styles: Optional[str] = None
    header_html: Optional[str] = None
    footer_html: Optional[str] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def validate_paper_dimensions(self):
        changed = self.model_fields_set
        if bool({"paper_width_mm", "paper_height_mm"} & changed) and not {
            "paper_width_mm",
            "paper_height_mm",
        }.issubset(changed):
            raise ValueError(
                "paper_width_mm and paper_height_mm must be updated together."
            )
        if (self.paper_width_mm is None) != (self.paper_height_mm is None):
            raise ValueError("paper_width_mm and paper_height_mm must be set together.")
        return self


class ReportTemplateForkRequest(BaseModel):
    new_code: str = Field(min_length=3, max_length=50, pattern=r"^[A-Z0-9_]+$")
    new_name: str = Field(min_length=3, max_length=100)
    set_as_default: bool = False
    print_mode: Optional[PrintModeEnum] = None


class ReportTemplatePreviewRequest(BaseModel):
    report_type: ReportTypeEnum
    print_mode: PrintModeEnum = PrintModeEnum.DESIGNED
    page_size: PageSizeEnum = PageSizeEnum.A4_PORTRAIT
    paper_width_mm: Optional[float] = Field(default=None, ge=10, le=1000)
    paper_height_mm: Optional[float] = Field(default=None, ge=10, le=1000)
    page_margin_top_mm: Optional[float] = Field(default=None, ge=0, le=100)
    page_margin_right_mm: Optional[float] = Field(default=None, ge=0, le=100)
    page_margin_bottom_mm: Optional[float] = Field(default=None, ge=0, le=100)
    page_margin_left_mm: Optional[float] = Field(default=None, ge=0, le=100)
    content_offset_x_mm: Optional[float] = Field(default=None, ge=-100, le=100)
    content_offset_y_mm: Optional[float] = Field(default=None, ge=-100, le=100)
    html_template: str
    css_styles: Optional[str] = ""
    header_html: Optional[str] = ""
    footer_html: Optional[str] = ""

    @model_validator(mode="after")
    def validate_paper_dimensions(self):
        if (self.paper_width_mm is None) != (self.paper_height_mm is None):
            raise ValueError("paper_width_mm and paper_height_mm must be set together.")
        return self


class ReportTemplateResponse(BaseModel):
    id: int
    report_type: ReportTypeEnum
    print_mode: PrintModeEnum
    code: str
    name: str
    description: Optional[str] = None
    page_size: PageSizeEnum
    paper_width_mm: Optional[float] = None
    paper_height_mm: Optional[float] = None
    page_margin_top_mm: Optional[float] = None
    page_margin_right_mm: Optional[float] = None
    page_margin_bottom_mm: Optional[float] = None
    page_margin_left_mm: Optional[float] = None
    content_offset_x_mm: Optional[float] = None
    content_offset_y_mm: Optional[float] = None
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
        "print_mode": tpl.print_mode,
        "code": tpl.code,
        "name": tpl.name,
        "description": tpl.description,
        "page_size": tpl.page_size,
        "paper_width_mm": tpl.paper_width_mm,
        "paper_height_mm": tpl.paper_height_mm,
        "page_margin_top_mm": tpl.page_margin_top_mm,
        "page_margin_right_mm": tpl.page_margin_right_mm,
        "page_margin_bottom_mm": tpl.page_margin_bottom_mm,
        "page_margin_left_mm": tpl.page_margin_left_mm,
        "content_offset_x_mm": tpl.content_offset_x_mm,
        "content_offset_y_mm": tpl.content_offset_y_mm,
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
            "printed_at": _fmt_local_time(datetime.now(timezone.utc)),
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
    _validate_printable_area(
        payload.paper_width_mm,
        payload.paper_height_mm,
        payload.page_margin_top_mm,
        payload.page_margin_right_mm,
        payload.page_margin_bottom_mm,
        payload.page_margin_left_mm,
    )

    # Validate template syntax & sandbox security before saving
    render_sandboxed_template(
        payload.html_template, payload.css_styles, payload.header_html, payload.footer_html, _build_sample_context()
    )

    if payload.is_default:
        prev_defaults = db.execute(
            select(ReportTemplate).where(
                ReportTemplate.report_type == payload.report_type,
                ReportTemplate.print_mode == payload.print_mode.value,
                ReportTemplate.is_default == True,
            )
        ).scalars().all()
        for pt in prev_defaults:
            pt.is_default = False

    tpl = ReportTemplate(
        report_type=payload.report_type,
        print_mode=payload.print_mode.value,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        page_size=payload.page_size,
        paper_width_mm=payload.paper_width_mm,
        paper_height_mm=payload.paper_height_mm,
        page_margin_top_mm=payload.page_margin_top_mm,
        page_margin_right_mm=payload.page_margin_right_mm,
        page_margin_bottom_mm=payload.page_margin_bottom_mm,
        page_margin_left_mm=payload.page_margin_left_mm,
        content_offset_x_mm=payload.content_offset_x_mm,
        content_offset_y_mm=payload.content_offset_y_mm,
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
                ReportTemplate.print_mode
                == (payload.print_mode or PrintModeEnum(source_tpl.print_mode)).value,
                ReportTemplate.is_default == True,
            )
        ).scalars().all()
        for pt in prev_defaults:
            pt.is_default = False

    forked = ReportTemplate(
        report_type=source_tpl.report_type,
        print_mode=(payload.print_mode or PrintModeEnum(source_tpl.print_mode)).value,
        code=payload.new_code,
        name=payload.new_name,
        description=f"Forked from {source_tpl.code}",
        page_size=source_tpl.page_size,
        paper_width_mm=source_tpl.paper_width_mm,
        paper_height_mm=source_tpl.paper_height_mm,
        page_margin_top_mm=source_tpl.page_margin_top_mm,
        page_margin_right_mm=source_tpl.page_margin_right_mm,
        page_margin_bottom_mm=source_tpl.page_margin_bottom_mm,
        page_margin_left_mm=source_tpl.page_margin_left_mm,
        content_offset_x_mm=source_tpl.content_offset_x_mm,
        content_offset_y_mm=source_tpl.content_offset_y_mm,
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

    _validate_printable_area(
        payload.paper_width_mm
        if "paper_width_mm" in payload.model_fields_set
        else tpl.paper_width_mm,
        payload.paper_height_mm
        if "paper_height_mm" in payload.model_fields_set
        else tpl.paper_height_mm,
        payload.page_margin_top_mm
        if "page_margin_top_mm" in payload.model_fields_set
        else tpl.page_margin_top_mm,
        payload.page_margin_right_mm
        if "page_margin_right_mm" in payload.model_fields_set
        else tpl.page_margin_right_mm,
        payload.page_margin_bottom_mm
        if "page_margin_bottom_mm" in payload.model_fields_set
        else tpl.page_margin_bottom_mm,
        payload.page_margin_left_mm
        if "page_margin_left_mm" in payload.model_fields_set
        else tpl.page_margin_left_mm,
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
    for field in (
        "paper_width_mm",
        "paper_height_mm",
        "page_margin_top_mm",
        "page_margin_right_mm",
        "page_margin_bottom_mm",
        "page_margin_left_mm",
        "content_offset_x_mm",
        "content_offset_y_mm",
    ):
        if field in payload.model_fields_set:
            setattr(tpl, field, getattr(payload, field))
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
            ReportTemplate.print_mode == tpl.print_mode,
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
    _validate_printable_area(
        payload.paper_width_mm,
        payload.paper_height_mm,
        payload.page_margin_top_mm,
        payload.page_margin_right_mm,
        payload.page_margin_bottom_mm,
        payload.page_margin_left_mm,
    )
    tenant = resolve_tenant_context(request)
    context = _build_sample_context(tenant.hospital_name, tenant.branch_code)
    context["tenant"]["print_mode"] = payload.print_mode.value
    context["tenant"]["page_size"] = payload.page_size.value
    template_parts = {
        "html_template": payload.html_template,
        "css_styles": payload.css_styles,
        "header_html": payload.header_html,
        "footer_html": payload.footer_html,
        "page_size": payload.page_size.value,
        "print_mode": payload.print_mode.value,
        "paper_width_mm": payload.paper_width_mm,
        "paper_height_mm": payload.paper_height_mm,
        "page_margin_top_mm": payload.page_margin_top_mm,
        "page_margin_right_mm": payload.page_margin_right_mm,
        "page_margin_bottom_mm": payload.page_margin_bottom_mm,
        "page_margin_left_mm": payload.page_margin_left_mm,
        "content_offset_x_mm": payload.content_offset_x_mm,
        "content_offset_y_mm": payload.content_offset_y_mm,
    }
    html = _render_template_parts(template_parts, context)
    return HTMLResponse(content=html, status_code=200)


# =====================================================================
# 5 LIVE PRINT ENDPOINTS (WITH DYNAMIC ?template_code= SWITCHING)
# =====================================================================

# 1. OPD Appointment & Consultation Case Sheet (with BMI/Vitals & Blank Doctor Writing Space)
@router.get(
    "/api/opd/appointments/{appointment_id}/print",
    response_class=HTMLResponse,
    openapi_extra={"x-privacy-data-use-point": "opd.appointment.print"},
)
def print_opd_appointment_case_sheet(
    appointment_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    print_mode: Optional[PrintModeEnum] = Query(default=None),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("opd:read", "adt:read")),
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
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="opd.appointment.print",
        patient_ids=[appt.patient_id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")

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
            "printed_at": _fmt_local_time(datetime.now(timezone.utc)),
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
            "appointment_time": _fmt_local_time(appt.appointment_time),
            "status": appt.status.value,
            "reason_for_visit": appt.reason_for_visit or "General Consultation",
        },
        "vitals": vitals_ctx,
        "allergy_banner": _get_patient_allergy_banner(db, patient.id),
    }

    tpl_parts = _resolve_template_for_print(
        db, ReportTypeEnum.OPD_APPOINTMENT_SLIP, template_code, print_mode
    )
    html = _render_template_parts(tpl_parts, context)
    return HTMLResponse(content=html, status_code=200)


# 2. Printable Tax Invoice & Payment Receipt
@router.get(
    "/api/billing/invoices/{invoice_id}/print",
    response_class=HTMLResponse,
    openapi_extra={"x-privacy-data-use-point": "billing.invoices.print"},
)
def print_invoice_and_receipt(
    invoice_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    print_mode: Optional[PrintModeEnum] = Query(default=None),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("billing:read")),
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
    data_use_point = (
        "billing.invoices.pdf"
        if request.url.path.endswith("/pdf")
        else "billing.invoices.print"
    )
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point=data_use_point,
        patient_ids=[invoice.patient_id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")

    balance_paise = invoice.net_amount_paise - invoice.amount_paid_paise
    claim = db.execute(select(InsuranceClaim).where(InsuranceClaim.invoice_id == invoice.id)).scalar_one_or_none()
    profile = _invoice_document_profile(db)
    format_amount = lambda value: _format_invoice_amount(value, profile)

    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_local_time(datetime.now(timezone.utc)),
            "document_profile": profile,
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
            "created_at": _fmt_local_time(invoice.created_at),
            "total_inr": format_amount(invoice.total_amount_paise),
            "discount_paise": invoice.discount_amount_paise or 0,
            "discount_inr": format_amount(invoice.discount_amount_paise or 0),
            "discount_reason": invoice.discount_reason or "None",
            "net_inr": format_amount(invoice.net_amount_paise),
            "paid_inr": format_amount(invoice.amount_paid_paise),
            "balance_inr": format_amount(balance_paise),
            "currency_code": profile["currency_code"],
            "tax_note": profile.get("tax_note") or "",
            "payment_terms": profile.get("payment_terms") or "",
        },
        "insurance_claim": {
            "claim_number": claim.claim_number if claim else "N/A",
            "status": claim.status.value if claim else "N/A",
            "insurer_payable_inr": format_amount(claim.insurer_payable_paise) if claim else format_amount(0),
            "patient_copay_inr": format_amount(claim.patient_copay_paise) if claim else format_amount(0),
            "settlement_utr_reference": (claim.settlement_utr_reference or "Pending") if claim else "N/A",
        },
        "line_items": [
            {
                "description": item.description,
                "quantity": item.quantity,
                "unit_price_inr": format_amount(item.unit_price_paise),
                "total_price_inr": format_amount(item.total_price_paise),
            }
            for item in invoice.line_items
        ],
        "payments": [
            {
                "receipt_number": p.receipt_number,
                "amount_inr": format_amount(p.amount_paid_paise),
                "payment_method": p.payment_method.value,
                "transaction_reference": p.transaction_reference or "N/A",
            }
            for p in invoice.payments
        ],
    }

    tpl_parts = _resolve_template_for_print(
        db, ReportTypeEnum.INVOICE, template_code, print_mode
    )
    html = _render_template_parts(tpl_parts, context)
    return HTMLResponse(content=html, status_code=200)


@router.get(
    "/api/billing/invoices/{invoice_id}/pdf",
    openapi_extra={"x-privacy-data-use-point": "billing.invoices.pdf"},
)
def download_invoice_pdf(
    invoice_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    print_mode: Optional[PrintModeEnum] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("billing:read")),
):
    html_response = print_invoice_and_receipt(
        invoice_id=invoice_id,
        request=request,
        template_code=template_code,
        print_mode=print_mode,
        db=db,
        actor=current_user,
    )
    try:
        pdf = render_html_pdf(html_response.body.decode("utf-8"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", f"invoice-{invoice_id}.pdf")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# 3. Printable eRx Medical Prescription
@router.get(
    "/api/pharmacy/prescriptions/{prescription_id}/print",
    response_class=HTMLResponse,
    openapi_extra={"x-privacy-data-use-point": "pharmacy.prescription.print"},
)
def print_erx_prescription(
    prescription_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    print_mode: Optional[PrintModeEnum] = Query(default=None),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("pharmacy:read", "opd:read", "adt:read")),
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
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="pharmacy.prescription.print",
        patient_ids=[patient.id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
    doc = rx.prescribing_doctor
    nmc_reg = doc.doctor_profile.medical_registration_number if doc.doctor_profile else "N/A"

    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_local_time(datetime.now(timezone.utc)),
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

    tpl_parts = _resolve_template_for_print(
        db, ReportTypeEnum.PRESCRIPTION, template_code, print_mode
    )
    html = _render_template_parts(tpl_parts, context)
    return HTMLResponse(content=html, status_code=200)


# 4. Printable Verified Diagnostic Lab & Radiology Report
@router.get(
    "/api/diagnostics/orders/{order_id}/print",
    response_class=HTMLResponse,
    openapi_extra={"x-privacy-data-use-point": "diagnostics.orders.print"},
)
def print_diagnostic_lab_report(
    order_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    print_mode: Optional[PrintModeEnum] = Query(default=None),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("diagnostics:read")),
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
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="diagnostics.orders.print",
        patient_ids=[order.patient_id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")

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
            "printed_at": _fmt_local_time(datetime.now(timezone.utc)),
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

    tpl_parts = _resolve_template_for_print(
        db, ReportTypeEnum.DIAGNOSTIC_REPORT, template_code, print_mode
    )
    html = _render_template_parts(tpl_parts, context)
    return HTMLResponse(content=html, status_code=200)


# 5. Printable IPD Clinical Discharge Summary
@router.get(
    "/api/adt/admissions/{admission_id}/discharge-summary/print",
    response_class=HTMLResponse,
    openapi_extra={"x-privacy-data-use-point": "adt.discharge_summary.print"},
)
def print_ipd_discharge_summary(
    admission_id: int,
    request: Request,
    template_code: Optional[str] = Query(default=None),
    print_mode: Optional[PrintModeEnum] = Query(default=None),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read")),
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
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="adt.discharge_summary.print",
        patient_ids=[patient.id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")

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
            "printed_at": _fmt_local_time(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": patient.full_name,
            "mrn": patient.mrn,
            "gender": patient.gender.value,
            "date_of_birth": patient.date_of_birth.strftime("%Y-%m-%d"),
        },
        "admission": {
            "admitting_doctor_name": admission.admitting_doctor.full_name,
            "discharge_time": _fmt_local_time(admission.discharge_time),
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

    tpl_parts = _resolve_template_for_print(
        db, ReportTypeEnum.DISCHARGE_SUMMARY, template_code, print_mode
    )
    html = _render_template_parts(tpl_parts, context)
    return HTMLResponse(content=html, status_code=200)