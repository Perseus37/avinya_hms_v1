import json
from typing import Any, Dict, List
from fastapi import HTTPException, status
from jinja2.exceptions import SecurityError, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from backend.domains.reports.models import PageSizeEnum, PrintModeEnum, ReportTypeEnum

# Strict Sandboxed Jinja2 Environment (blocks SSTI / arbitrary Python attribute access)
_sandbox_env = SandboxedEnvironment(autoescape=True)

def render_sandboxed_template(
    html_template: str,
    css_styles: str | None,
    header_html: str | None,
    footer_html: str | None,
    context: Dict[str, Any],
    page_size: str | None = None,
    print_mode: str | None = None,
    paper_width_mm: float | None = None,
    paper_height_mm: float | None = None,
    page_margins_mm: Dict[str, float | None] | None = None,
    content_offset_x_mm: float | None = None,
    content_offset_y_mm: float | None = None,
) -> str:
    """Safely compiles and renders a tenant's HTML/CSS template inside a Jinja2 Sandbox."""
    try:
        body_rendered = _sandbox_env.from_string(html_template).render(**context)
        header_rendered = _sandbox_env.from_string(header_html).render(**context) if header_html else ""
        footer_rendered = _sandbox_env.from_string(footer_html).render(**context) if footer_html else ""
    except SecurityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Template security violation blocked by sandbox: {str(exc)}",
        )
    except TemplateError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Template syntax error: {str(exc)}",
        )

    data_only = print_mode == PrintModeEnum.PREPRINTED_DATA_ONLY.value
    page_rules = {
        PageSizeEnum.A4_PORTRAIT.value: (
            "@page { size: A4 portrait; margin: 0; }"
            if data_only
            else "@page { size: A4 portrait; margin: 10mm; }"
        ),
        PageSizeEnum.A4_LANDSCAPE.value: (
            "@page { size: A4 landscape; margin: 0; }"
            if data_only
            else "@page { size: A4 landscape; margin: 10mm; }"
        ),
        PageSizeEnum.A5_PORTRAIT.value: (
            "@page { size: A5 portrait; margin: 0; }"
            if data_only
            else "@page { size: A5 portrait; margin: 8mm; }"
        ),
        PageSizeEnum.THERMAL_80MM.value: (
            "@page { size: 80mm 297mm; margin: 0; }"
            if data_only
            else "@page { size: 80mm 297mm; margin: 2mm; }"
        ),
    }
    margins = page_margins_mm or {}
    page_sizes = {
        PageSizeEnum.A4_PORTRAIT.value: "A4 portrait",
        PageSizeEnum.A4_LANDSCAPE.value: "A4 landscape",
        PageSizeEnum.A5_PORTRAIT.value: "A5 portrait",
        PageSizeEnum.THERMAL_80MM.value: "80mm 297mm",
    }
    default_margin = 0 if data_only else {
        PageSizeEnum.A4_PORTRAIT.value: 10,
        PageSizeEnum.A4_LANDSCAPE.value: 10,
        PageSizeEnum.A5_PORTRAIT.value: 8,
        PageSizeEnum.THERMAL_80MM.value: 2,
    }.get(page_size or "", 0)
    top = margins.get("top")
    right = margins.get("right")
    bottom = margins.get("bottom")
    left = margins.get("left")
    if paper_width_mm is not None and paper_height_mm is not None:
        page_rule = (
            f"@page {{ size: {paper_width_mm:g}mm {paper_height_mm:g}mm; "
            f"margin: {top if top is not None else default_margin:g}mm "
            f"{right if right is not None else default_margin:g}mm "
            f"{bottom if bottom is not None else default_margin:g}mm "
            f"{left if left is not None else default_margin:g}mm; }}"
        )
    elif any(value is not None for value in margins.values()):
        page_rule = (
            f"@page {{ size: {page_sizes.get(page_size or '', 'A4 portrait')}; "
            f"margin: {top if top is not None else default_margin:g}mm "
            f"{right if right is not None else default_margin:g}mm "
            f"{bottom if bottom is not None else default_margin:g}mm "
            f"{left if left is not None else default_margin:g}mm; }}"
        )
    else:
        page_rule = page_rules.get(page_size or "")
    base_styles = (
        "body { font-family: Arial, sans-serif; color: #000; margin: 0; }"
        if data_only
        else "body { font-family: Arial, sans-serif; color: #1e293b; margin: 20px; }"
    )
    if not data_only:
        base_styles += (
            "table { width: 100%; border-collapse: collapse; margin-top: 10px; }"
            "th, td { border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; }"
            "th { background-color: #f8fafc; }"
        )
    if content_offset_x_mm is not None or content_offset_y_mm is not None:
        base_styles += (
            "body { position: relative; "
            f"left: {content_offset_x_mm or 0:g}mm; "
            f"top: {content_offset_y_mm or 0:g}mm; }}"
        )
    css_block = "\n".join(
        value for value in (base_styles, page_rule, css_styles or "") if value
    )
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    {css_block}
  </style>
</head>
<body>
  {header_rendered}
  {body_rendered}
  {footer_rendered}
</body>
</html>"""

# =====================================================================
# DRAG-AND-DROP VARIABLE CATALOG & SAMPLE DATA FOR LIVE PREVIEW
# =====================================================================

COMMON_GROUPS: List[Dict[str, Any]] = [
    {
        "group_name": "Hospital Branding",
        "variables": [
            {"label": "Hospital Name", "token": "{{ tenant.hospital_name }}", "sample": "Apollo Hospital"},
            {"label": "Branch Code", "token": "{{ tenant.branch_code }}", "sample": "APOLLO-HYD-01"},
            {"label": "Printed Timestamp", "token": "{{ tenant.printed_at }}", "sample": "2026-10-02 14:45 IST"},
        ],
    },
    {
        "group_name": "Patient Demographics",
        "variables": [
            {"label": "Patient Full Name", "token": "{{ patient.full_name }}", "sample": "Vikram Rao"},
            {"label": "Patient MRN", "token": "{{ patient.mrn }}", "sample": "MRN-516ED031"},
            {"label": "Gender", "token": "{{ patient.gender }}", "sample": "MALE"},
            {"label": "Date of Birth", "token": "{{ patient.date_of_birth }}", "sample": "1988-04-12"},
        ],
    },
]

VARIABLE_CATALOG: Dict[ReportTypeEnum, List[Dict[str, Any]]] = {
    ReportTypeEnum.OPD_APPOINTMENT_SLIP: COMMON_GROUPS + [
        {
            "group_name": "Appointment & Consulting Doctor",
            "variables": [
                {"label": "Appointment ID", "token": "{{ appointment.id }}", "sample": "104"},
                {"label": "Appointment Time", "token": "{{ appointment.appointment_time }}", "sample": "2026-10-02 15:00 IST"},
                {"label": "Appointment Status", "token": "{{ appointment.status }}", "sample": "ARRIVED"},
                {"label": "Reason for Visit", "token": "{{ appointment.reason_for_visit }}", "sample": "Fever and joint pain"},
                {"label": "Doctor Name", "token": "{{ doctor.full_name }}", "sample": "Dr. Gregory House"},
                {"label": "Qualification & Spec", "token": "{{ doctor.qualification }} ({{ doctor.specialization }})", "sample": "MD Internal Medicine (General Medicine)"},
                {"label": "Medical Council Reg No", "token": "{{ doctor.nmc_reg }}", "sample": "NMC-2026-001"},
                {"label": "Department Name", "token": "{{ department.name }}", "sample": "General Medicine"},
            ],
        },
        {
            "group_name": "Triage Vitals & BMI Details",
            "variables": [
                {"label": "Weight (kg)", "token": "{{ vitals.weight_kg }}", "sample": "72.0 kg"},
                {"label": "Height (cm)", "token": "{{ vitals.height_cm }}", "sample": "180.0 cm"},
                {"label": "Calculated BMI", "token": "{{ vitals.bmi }}", "sample": "22.22 kg/m²"},
                {"label": "Blood Pressure (mmHg)", "token": "{{ vitals.bp }}", "sample": "120/80 mmHg"},
                {"label": "Pulse (bpm)", "token": "{{ vitals.pulse_bpm }}", "sample": "76 bpm"},
                {"label": "Temperature (°F)", "token": "{{ vitals.temperature_f }}", "sample": "98.6 °F"},
                {"label": "SpO2 (%)", "token": "{{ vitals.spo2_percent }}", "sample": "99%"},
                {"label": "Respiratory Rate", "token": "{{ vitals.respiratory_rate }}", "sample": "16 /min"},
                {"label": "Allergy Alert Banner", "token": "{{ allergy_banner }}", "sample": "Penicillin / Beta-Lactams (ANAPHYLACTIC)"},
            ],
        },
    ],
    ReportTypeEnum.INVOICE: COMMON_GROUPS + [
        {
            "group_name": "Invoice Totals, Discounts & Net Payable",
            "variables": [
                {"label": "Invoice Number", "token": "{{ invoice.invoice_number }}", "sample": "INV-20261002-A1B2"},
                {"label": "Invoice Status", "token": "{{ invoice.status }}", "sample": "PAID"},
                {"label": "Gross Total (₹)", "token": "{{ invoice.total_inr }}", "sample": "₹10,000.00"},
                {"label": "Discount / Concession (₹)", "token": "{{ invoice.discount_inr }}", "sample": "₹1,000.00"},
                {"label": "Discount Reason", "token": "{{ invoice.discount_reason }}", "sample": "Senior Citizen 10% Concession"},
                {"label": "Net Payable Amount (₹)", "token": "{{ invoice.net_inr }}", "sample": "₹9,000.00"},
                {"label": "Amount Paid (₹)", "token": "{{ invoice.paid_inr }}", "sample": "₹9,000.00"},
                {"label": "Balance Due (₹)", "token": "{{ invoice.balance_inr }}", "sample": "₹0.00"},
                {"label": "Currency Code", "token": "{{ invoice.currency_code }}", "sample": "INR"},
                {"label": "Configured Tax Note", "token": "{{ invoice.tax_note }}", "sample": "GST registration details"},
            ],
        },
        {
            "group_name": "Insurance & TPA Claim Split (If Applicable)",
            "variables": [
                {"label": "Claim Number", "token": "{{ insurance_claim.claim_number }}", "sample": "CLM-20261002-99A1"},
                {"label": "Claim Status", "token": "{{ insurance_claim.status }}", "sample": "SETTLED"},
                {"label": "Insurer Approved Payable (₹)", "token": "{{ insurance_claim.insurer_payable_inr }}", "sample": "₹7,200.00"},
                {"label": "Patient Co-Pay Share (₹)", "token": "{{ insurance_claim.patient_copay_inr }}", "sample": "₹1,800.00"},
                {"label": "TPA / Insurer UTR Ref", "token": "{{ insurance_claim.settlement_utr_reference }}", "sample": "NEFT-STAR-UTR9988"},
            ],
        },
        {
            "group_name": "Line Items Table",
            "is_table": True,
            "iterator": "line_items",
            "columns": [
                {"label": "Description", "token": "{{ item.description }}"},
                {"label": "Qty", "token": "{{ item.quantity }}"},
                {"label": "Unit Price", "token": "{{ item.unit_price_inr }}"},
                {"label": "Total Price", "token": "{{ item.total_price_inr }}"},
            ],
        },
        {
            "group_name": "Payment & Advance Adjustment Receipts",
            "is_table": True,
            "iterator": "payments",
            "columns": [
                {"label": "Receipt Number", "token": "{{ p.receipt_number }}"},
                {"label": "Amount Paid (₹)", "token": "{{ p.amount_inr }}"},
                {"label": "Payment Method (CASH/UPI/INSURANCE/ADVANCE_ADJUSTMENT)", "token": "{{ p.payment_method }}"},
                {"label": "Transaction / Deposit Ref", "token": "{{ p.transaction_reference }}"},
            ],
        },
    ],
    ReportTypeEnum.PRESCRIPTION: COMMON_GROUPS + [
        {
            "group_name": "Doctor & Safety Details",
            "variables": [
                {"label": "Doctor Name", "token": "{{ doctor.full_name }}", "sample": "Dr. Gregory House"},
                {"label": "NMC Reg Number", "token": "{{ doctor.nmc_reg }}", "sample": "NMC-2026-001"},
                {"label": "Allergy Banner", "token": "{{ allergy_banner }}", "sample": "Sulfonamides (SEVERE)"},
                {"label": "Clinical Notes", "token": "{{ prescription.clinical_notes }}", "sample": "Take after meals"},
            ],
        },
        {
            "group_name": "Medications Table",
            "is_table": True,
            "iterator": "medications",
            "columns": [
                {"label": "Brand Name", "token": "{{ med.brand_name }}"},
                {"label": "Generic & Strength", "token": "{{ med.generic_name }} {{ med.strength }}"},
                {"label": "Dosage Instruction", "token": "{{ med.dosage_instruction }}"},
                {"label": "Quantity", "token": "{{ med.requested_quantity }}"},
            ],
        },
    ],
    ReportTypeEnum.DIAGNOSTIC_REPORT: COMMON_GROUPS + [
        {
            "group_name": "Diagnostic Order Metadata",
            "variables": [
                {"label": "Order ID", "token": "{{ order.id }}", "sample": "42"},
                {"label": "Ordering Doctor", "token": "{{ order.ordering_doctor_name }}", "sample": "Dr. Gregory House"},
                {"label": "Clinical Notes", "token": "{{ order.clinical_notes }}", "sample": "Rule out anemia / pulmonary embolism"},
            ],
        },
        {
            "group_name": "Verified Lab & Radiology PACS Tasks",
            "is_table": True,
            "iterator": "tasks",
            "columns": [
                {"label": "Test Code & Name", "token": "{{ task.service_code }} - {{ task.service_name }}"},
                {"label": "Result Value", "token": "{{ task.result_value }}"},
                {"label": "Reference / Notes", "token": "{{ task.result_notes }}"},
                {"label": "Performed By", "token": "{{ task.performed_by_name }}"},
                {"label": "Verified By", "token": "{{ task.verified_by_name }}"},
                {"label": "DICOM Modality", "token": "{{ task.dicom_modality }}"},
                {"label": "DICOM Accession No", "token": "{{ task.dicom_accession_number }}"},
                {"label": "PACS Viewer URL", "token": "{{ task.pacs_viewer_url }}"},
                {"label": "Radiologist Impression", "token": "{{ task.radiologist_impression }}"},
            ],
        },
    ],
    ReportTypeEnum.DISCHARGE_SUMMARY: COMMON_GROUPS + [
        {
            "group_name": "Admission & Discharge Metadata",
            "variables": [
                {"label": "Admitting Doctor", "token": "{{ admission.admitting_doctor_name }}", "sample": "Dr. Gregory House"},
                {"label": "Discharged Timestamp", "token": "{{ admission.discharge_time }}", "sample": "2026-10-02 14:00 IST"},
                {"label": "Discharge Disposition", "token": "{{ admission.discharge_disposition }}", "sample": "ROUTINE_DISCHARGE"},
                {"label": "Discharge Instructions", "token": "{{ admission.discharge_summary }}", "sample": "Follow up in OPD after 7 days."},
            ],
        },
        {
            "group_name": "Encounter ICD-10 Diagnoses",
            "is_table": True,
            "iterator": "diagnoses",
            "columns": [
                {"label": "Diagnosis Type (PRIMARY/SECONDARY)", "token": "{{ d.diagnosis_type }}"},
                {"label": "ICD-10 Code", "token": "{{ d.icd10_code }}"},
                {"label": "Description", "token": "{{ d.short_description }}"},
            ],
        },
    ],
    ReportTypeEnum.MLC_POLICE_REPORT: COMMON_GROUPS + [
        {
            "group_name": "Medico-Legal Case & Police Intimation Details",
            "variables": [
                {"label": "MLC Number", "token": "{{ mlc.mlc_number }}", "sample": "MLC-20261002-88F1"},
                {"label": "Cause of Injury", "token": "{{ mlc.cause_of_injury }}", "sample": "ROAD_TRAFFIC_ACCIDENT"},
                {"label": "Alleged History", "token": "{{ mlc.alleged_history }}", "sample": "Two-wheeler collision near Hitech City"},
                {"label": "Identification Marks", "token": "{{ mlc.identification_marks }}", "sample": "Mole on right cheek"},
                {"label": "Brought By Name & Relation", "token": "{{ mlc.brought_by_name }} ({{ mlc.brought_by_relation }})", "sample": "Ramesh Kumar (Brother)"},
                {"label": "Police Station Name", "token": "{{ mlc.police_station_name }}", "sample": "Madhapur Police Station"},
                {"label": "Constable Buckle No", "token": "{{ mlc.police_constable_buckle_no }}", "sample": "PC-4421-CYB"},
                {"label": "FIR Number", "token": "{{ mlc.fir_number }}", "sample": "FIR-2026-0891"},
                {"label": "Police Intimated Timestamp", "token": "{{ mlc.police_intimated_at }}", "sample": "2026-10-02 20:15 IST"},
                {"label": "Casualty Medical Officer", "token": "{{ mlc.doctor_name }}", "sample": "Dr. Gregory House"},
            ],
        },
    ],
    ReportTypeEnum.SURGICAL_OPERATIVE_NOTE: COMMON_GROUPS + [
        {
            "group_name": "Surgery, Theatre & Anesthesia Details",
            "variables": [
                {"label": "Surgical Case Number", "token": "{{ surgery.case_number }}", "sample": "OT-20261002-11A2"},
                {"label": "Operation Theatre", "token": "{{ surgery.theatre_name }}", "sample": "Modular Theatre 1 (OT_MOD_1)"},
                {"label": "Procedure Name", "token": "{{ surgery.procedure_name }}", "sample": "Total Hip Arthroplasty"},
                {"label": "Lead Surgeon", "token": "{{ surgery.surgeon_name }}", "sample": "Dr. Gregory House"},
                {"label": "Anesthesia Type", "token": "{{ record.anesthesia_type }}", "sample": "SPINAL"},
                {"label": "ASA Physical Status Grade", "token": "{{ record.asa_physical_status }}", "sample": "2"},
                {"label": "Estimated Blood Loss (mL)", "token": "{{ record.estimated_blood_loss_ml }}", "sample": "250"},
                {"label": "Operative Findings", "token": "{{ record.operative_findings }}", "sample": "Prosthesis seated with good stability."},
                {"label": "Post-Op Instructions", "token": "{{ record.post_op_instructions }}", "sample": "Limb abduction pillow, DVT prophylaxis."},
            ],
        },
        {
            "group_name": "Surgical Implants & Prosthetics Table",
            "is_table": True,
            "iterator": "implants",
            "columns": [
                {"label": "Implant Name", "token": "{{ imp.implant_name }}"},
                {"label": "Manufacturer", "token": "{{ imp.manufacturer }}"},
                {"label": "Serial / Lot Number", "token": "{{ imp.serial_or_batch_number }}"},
                {"label": "Quantity", "token": "{{ imp.quantity }}"},
            ],
        },
    ],
    ReportTypeEnum.BIRTH_CERTIFICATE: COMMON_GROUPS + [
        {
            "group_name": "Maternity & Newborn Details",
            "variables": [
                {"label": "Birth Certificate No", "token": "{{ birth.certificate_number }}", "sample": "BIRTH-20261003-01"},
                {"label": "Newborn MRN", "token": "{{ birth.newborn_mrn }}", "sample": "MRN-NB-001"},
                {"label": "Birth Weight (kg)", "token": "{{ birth.birth_weight_kg }}", "sample": "3.2"},
                {"label": "APGAR Score (1m/5m)", "token": "{{ birth.apgar_1min }}/{{ birth.apgar_5min }}", "sample": "8/9"},
            ],
        }
    ],
    ReportTypeEnum.DEATH_CERTIFICATE_MCCD: COMMON_GROUPS + [
        {
            "group_name": "MCCD Cause of Death Details",
            "variables": [
                {"label": "MCCD Certificate No", "token": "{{ death.certificate_number }}", "sample": "MCCD-20261003-01"},
                {"label": "Immediate Cause ICD-10", "token": "{{ death.icd10_code }}", "sample": "I21.9"},
                {"label": "Manner of Death", "token": "{{ death.manner_of_death }}", "sample": "NATURAL"},
            ],
        }
    ],
    ReportTypeEnum.INSURANCE_PREAUTH: COMMON_GROUPS + [
        {
            "group_name": "Payer Pre-Authorization",
            "variables": [
                {"label": "Pre-authorization Number", "token": "{{ preauth.preauth_number }}"},
                {"label": "Requested Amount", "token": "{{ preauth.requested_amount }}"},
                {"label": "Clinical Justification", "token": "{{ preauth.clinical_justification }}"},
                {"label": "Diagnosis", "token": "{{ diagnosis.code }} — {{ diagnosis.description }}"},
                {"label": "Policy Number", "token": "{{ policy.policy_number }}"},
                {"label": "Member ID", "token": "{{ policy.member_id }}"},
                {"label": "Payer", "token": "{{ provider.name }}"},
            ],
        },
        {
            "group_name": "Payer Required Documents",
            "is_table": True,
            "iterator": "required_documents",
            "columns": [{"label": "Document", "token": "{{ item }}"}],
        },
    ],
    ReportTypeEnum.INSURANCE_CLAIM: COMMON_GROUPS + [
        {
            "group_name": "Payer Claim & Invoice",
            "variables": [
                {"label": "Claim Number", "token": "{{ claim.claim_number }}"},
                {"label": "Claimed Amount", "token": "{{ claim.claimed_amount }}"},
                {"label": "Insurer Payable", "token": "{{ claim.insurer_payable }}"},
                {"label": "Patient Co-pay", "token": "{{ claim.patient_copay }}"},
                {"label": "Final Invoice", "token": "{{ invoice.invoice_number }}"},
                {"label": "Payer", "token": "{{ provider.name }}"},
                {"label": "Policy Number", "token": "{{ policy.policy_number }}"},
            ],
        },
        {
            "group_name": "Final Itemized Charges",
            "is_table": True,
            "iterator": "line_items",
            "columns": [
                {"label": "Description", "token": "{{ item.description }}"},
                {"label": "Qty", "token": "{{ item.quantity }}"},
                {"label": "Unit Price", "token": "{{ item.unit_price }}"},
                {"label": "Total", "token": "{{ item.total_price }}"},
            ],
        },
        {
            "group_name": "Payer Required Documents",
            "is_table": True,
            "iterator": "required_documents",
            "columns": [{"label": "Document", "token": "{{ item }}"}],
        },
    ],
    ReportTypeEnum.PURCHASE_ORDER: COMMON_GROUPS + [
        {
            "group_name": "Purchase Order Details",
            "variables": [
                {"label": "PO Number", "token": "{{ po.po_number }}", "sample": "PO-20261011-A1B2"},
                {"label": "PO Status", "token": "{{ po.status }}", "sample": "APPROVED"},
                {"label": "Delivery Due Date", "token": "{{ po.delivery_due_date }}", "sample": "2026-10-25"},
                {"label": "Payment Terms", "token": "{{ po.payment_terms }}", "sample": "Net 30 Days"},
                {"label": "Total Amount", "token": "{{ po.total_amount }}", "sample": " 50,000.00"},
            ],
        },
        {
            "group_name": "Vendor Details",
            "variables": [
                {"label": "Vendor Name", "token": "{{ vendor.name }}", "sample": "MediEquip Inc."},
                {"label": "Vendor Code", "token": "{{ vendor.code }}", "sample": "VND-MEQ-01"},
                {"label": "Vendor Address", "token": "{{ vendor.address }}", "sample": "123 Industrial Park, Hyderabad"},
                {"label": "Vendor GSTIN", "token": "{{ vendor.gstin }}", "sample": "36AAAAA1234A1Z5"},
                {"label": "Contact Person", "token": "{{ vendor.contact_person }}", "sample": "Ramesh Kumar"},
                {"label": "Contact Phone", "token": "{{ vendor.phone }}", "sample": "+91-9876543210"},
            ],
        },
        {
            "group_name": "Itemized PO Lines",
            "is_table": True,
            "iterator": "items",
            "columns": [
                {"label": "Description", "token": "{{ item.description }}"},
                {"label": "Qty Ordered", "token": "{{ item.quantity_ordered }}"},
                {"label": "Unit Price", "token": "{{ item.unit_price }}"},
                {"label": "Total Price", "token": "{{ item.total_price }}"},
            ],
        },
    ],
}


# =====================================================================
# 6 PRE-SEEDED STANDARD HOSPITAL TEMPLATES
# =====================================================================

STANDARD_TEMPLATES: List[Dict[str, Any]] = [
    # 1. OPD Appointment & Clinical Case Sheet (with BMI/Vitals & Blank Doctor Writing Space)
    {
        "report_type": ReportTypeEnum.OPD_APPOINTMENT_SLIP,
        "code": "SYS_OPD_CASE_SHEET_A4",
        "name": "Standard A4 OPD Case Sheet (with BMI Vitals & Doctor Writing Space)",
        "description": "Pre-filled patient demographics, BMI/vitals strip, and a large structured blank area for handwritten doctor consultation notes.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [
                {"id": "hdr", "type": "header", "x": 0, "y": 0, "w": 12, "h": 2, "fields": ["tenant.hospital_name", "tenant.branch_code"]},
                {"id": "pat_box", "type": "box", "x": 0, "y": 2, "w": 6, "h": 3, "fields": ["patient.full_name", "patient.mrn", "patient.gender"]},
                {"id": "doc_box", "type": "box", "x": 6, "y": 2, "w": 6, "h": 3, "fields": ["doctor.full_name", "doctor.nmc_reg", "appointment.appointment_time"]},
                {"id": "vitals_bmi", "type": "vitals_strip", "x": 0, "y": 5, "w": 12, "h": 2, "fields": ["vitals.weight_kg", "vitals.height_cm", "vitals.bmi", "vitals.bp", "vitals.pulse_bpm", "vitals.spo2_percent", "vitals.temperature_f"]},
                {"id": "doctor_canvas", "type": "handwriting_area", "x": 0, "y": 7, "w": 12, "h": 14, "min_height_px": 480, "split_columns": ["Chief Complaints & Exam", "Rx / Treatment Plan"]},
            ],
        }),
        "css_styles": """
            .vitals-grid { display: table; width: 100%; border: 2px solid #0f172a; margin-top: 10px; background: #f8fafc; }
            .vitals-cell { display: table-cell; padding: 8px; border-right: 1px solid #cbd5e1; font-size: 13px; }
            .bmi-highlight { font-weight: bold; color: #0369a1; background: #e0f2fe; }
            .writing-canvas { width: 100%; height: 480px; border: 2px solid #0f172a; margin-top: 12px; display: table; }
            .left-clinical-margin { display: table-cell; width: 32%; border-right: 2px dashed #64748b; padding: 12px; vertical-align: top; }
            .right-rx-space { display: table-cell; width: 68%; padding: 12px; vertical-align: top; }
            .rx-symbol { font-size: 28px; font-weight: bold; margin-bottom: 10px; }
            .sign-footer { margin-top: 24px; text-align: right; font-weight: bold; }
        """,
        "header_html": """
            <div style="border-bottom: 2px solid #0f172a; padding-bottom: 8px; margin-bottom: 10px;">
              <h1 style="margin: 0;">{{ tenant.hospital_name }} ({{ tenant.branch_code }})</h1>
              <p style="margin: 2px 0;"><strong>OPD CONSULTATION CASE SHEET &amp; APPOINTMENT SLIP</strong> | Printed: {{ tenant.printed_at }}</p>
            </div>
        """,
        "html_template": """
            <table>
              <tr>
                <td><strong>Patient Name:</strong> {{ patient.full_name }}</td>
                <td><strong>MRN:</strong> {{ patient.mrn }}</td>
                <td><strong>Gender / DOB:</strong> {{ patient.gender }} ({{ patient.date_of_birth }})</td>
              </tr>
              <tr>
                <td><strong>Consulting Doctor:</strong> {{ doctor.full_name }} ({{ doctor.qualification }})</td>
                <td><strong>Reg No:</strong> {{ doctor.nmc_reg }} | <strong>Dept:</strong> {{ department.name }}</td>
                <td><strong>Appt Time:</strong> {{ appointment.appointment_time }} (#{{ appointment.id }})</td>
              </tr>
            </table>

            <div style="color: #b91c1c; font-weight: bold; margin-top: 8px; padding: 4px 8px; border: 1px solid #ef4444; background: #fef2f2;">
              ALLERGY ALERT: {{ allergy_banner }}
            </div>

            <div class="vitals-grid">
              <div class="vitals-cell"><strong>Weight:</strong> {{ vitals.weight_kg }}</div>
              <div class="vitals-cell"><strong>Height:</strong> {{ vitals.height_cm }}</div>
              <div class="vitals-cell bmi-highlight"><strong>BMI:</strong> {{ vitals.bmi }}</div>
              <div class="vitals-cell"><strong>BP:</strong> {{ vitals.bp }}</div>
              <div class="vitals-cell"><strong>Pulse:</strong> {{ vitals.pulse_bpm }}</div>
              <div class="vitals-cell"><strong>SpO2:</strong> {{ vitals.spo2_percent }}</div>
              <div class="vitals-cell"><strong>Temp:</strong> {{ vitals.temperature_f }}</div>
            </div>

            <p style="margin: 8px 0 4px 0;"><strong>Presenting Complaint / Visit Reason:</strong> {{ appointment.reason_for_visit }}</p>

            <!-- Structured Empty Space for Doctor to Write -->
            <div class="writing-canvas">
              <div class="left-clinical-margin">
                <strong>History &amp; Physical Exam Findings:</strong>
                <br/><br/><br/><br/><br/><br/><br/><br/><br/><br/>
                <strong>Investigations Advised:</strong>
                <br/><br/><br/><br/><br/><br/>
                <strong>Provisional Diagnosis:</strong>
              </div>
              <div class="right-rx-space">
                <div class="rx-symbol">&#8478; (Clinical Notes &amp; Prescription)</div>
              </div>
            </div>
        """,
        "footer_html": """
            <div class="sign-footer">
              <p>Next Follow-Up Date: ____________________ &nbsp;&nbsp;&nbsp;&nbsp; Doctor's Signature &amp; Stamp: ________________________</p>
            </div>
        """,
    },

    # 2. Standard A4 Tax Invoice & Payment Receipt
    {
        "report_type": ReportTypeEnum.INVOICE,
        "code": "SYS_INV_A4_CLASSIC",
        "name": "Standard A4 Tax Invoice & Payment Receipt",
        "description": "Full-page itemized hospital tax invoice with payment receipt breakdown.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [
                {"id": "hdr", "type": "header", "x": 0, "y": 0, "w": 12, "h": 2},
                {"id": "items_tbl", "type": "table", "x": 0, "y": 3, "w": 12, "h": 6, "iterator": "line_items"},
                {"id": "totals", "type": "summary", "x": 7, "y": 9, "w": 5, "h": 3},
            ],
        }),
        "css_styles": "",
        "header_html": """
            <h1>{{ tenant.document_profile.legal_entity_name or tenant.hospital_name }} ({{ tenant.branch_code }})</h1>
            {% for line in tenant.document_profile.address_lines %}<div>{{ line }}</div>{% endfor %}
            {% for tax in tenant.document_profile.tax_registrations %}<div>{{ tax.jurisdiction }} — {{ tax.label }}: {{ tax.registration_number }}</div>{% endfor %}
            <h2>TAX INVOICE &amp; PAYMENT RECEIPT — {{ invoice.invoice_number }}</h2>
        """,
        "html_template": """
            <p><strong>Status:</strong> {{ invoice.status }} | <strong>Date:</strong> {{ invoice.created_at }}</p>
            <p><strong>Patient:</strong> {{ patient.full_name }} | <strong>MRN:</strong> {{ patient.mrn }}</p>
            <table>
              <thead><tr><th>#</th><th>Description</th><th>Qty</th><th>Unit Price</th><th>Total</th></tr></thead>
              <tbody>
                {% for item in line_items %}
                <tr>
                  <td>{{ loop.index }}</td>
                  <td>{{ item.description }}</td>
                  <td>{{ item.quantity }}</td>
                  <td>{{ item.unit_price_inr }}</td>
                  <td>{{ item.total_price_inr }}</td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
            <p><strong>Gross Amount ({{ invoice.currency_code }}):</strong> {{ invoice.total_inr }}</p>
            {% if invoice.discount_paise and invoice.discount_paise > 0 %}
            <p><strong>Discount / Concession ({{ invoice.discount_reason }}):</strong> -{{ invoice.discount_inr }}</p>
            {% endif %}
            <p><strong>Net Payable Amount ({{ invoice.currency_code }}):</strong> {{ invoice.net_inr }}</p>
            {% if invoice.tax_note %}<p><strong>Tax / fiscal note:</strong> {{ invoice.tax_note }}</p>{% endif %}
            {% if insurance_claim and insurance_claim.claim_number != 'N/A' %}
            <p><strong>Insurance Claim ({{ insurance_claim.claim_number }} - {{ insurance_claim.status }}):</strong> Insurer Share: {{ insurance_claim.insurer_payable_inr }} | Patient Co-Pay: {{ insurance_claim.patient_copay_inr }}</p>
            {% endif %}
            <p><strong>Amount Paid:</strong> {{ invoice.paid_inr }}</p>
            <p><strong>Balance Due:</strong> {{ invoice.balance_inr }}</p>
            {% if invoice.payment_terms %}<p><strong>Payment terms:</strong> {{ invoice.payment_terms }}</p>{% endif %}
            <h3>Payment Receipts</h3>
            <ul>
              {% for p in payments %}
              <li>Receipt <strong>{{ p.receipt_number }}</strong>: {{ p.amount_inr }} via {{ p.payment_method }}</li>
              {% else %}
              <li>No payments recorded</li>
              {% endfor %}
            </ul>
        """,
        "footer_html": "<p style='font-size:12px;color:#64748b;'>Authoritative computer-generated hospital invoice.</p>",
    },

    # 3. 80mm Thermal POS Receipt Slip (Switchable Alternative for Billing Desk)
    {
        "report_type": ReportTypeEnum.INVOICE,
        "code": "SYS_INV_THERMAL_80MM",
        "name": "80mm Thermal POS Receipt Slip",
        "description": "Compact 80mm thermal roll layout for quick front-desk POS receipt printing.",
        "page_size": PageSizeEnum.THERMAL_80MM,
        "is_default": False,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 80, "height_mm": 200, "grid_columns": 4},
            "widgets": [{"id": "pos_slip", "type": "thermal_block", "x": 0, "y": 0, "w": 4, "h": 10}],
        }),
        "css_styles": "body { width: 300px; font-family: monospace; font-size: 12px; }",
        "header_html": "<div style='text-align:center;'><strong>{{ tenant.hospital_name }}</strong><br/>Branch: {{ tenant.branch_code }}<br/>THERMAL POS RECEIPT</div><hr/>",
        "html_template": """
            <div>Bill: {{ invoice.invoice_number }}</div>
            <div>MRN: {{ patient.mrn }} | {{ patient.full_name }}</div>
            <hr/>
            {% for item in line_items %}
            <div>{{ item.description }} x{{ item.quantity }} = {{ item.total_price_inr }}</div>
            {% endfor %}
            <hr/>
            <div><strong>TOTAL: {{ invoice.total_inr }}</strong></div>
            <div>PAID: {{ invoice.paid_inr }} | DUE: {{ invoice.balance_inr }}</div>
        """,
        "footer_html": "<hr/><div style='text-align:center;'>Thank You — Get Well Soon</div>",
    },

    # 4. Standard A4 eRx Medical Prescription
    {
        "report_type": ReportTypeEnum.PRESCRIPTION,
        "code": "SYS_ERX_LETTERHEAD_A4",
        "name": "Standard A4 Medical Prescription (eRx)",
        "description": "Electronic prescription with NMC registration number and allergy safety banner.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [
                {"id": "hdr", "type": "header", "x": 0, "y": 0, "w": 12, "h": 2},
                {"id": "allergy_bar", "type": "alert", "x": 0, "y": 2, "w": 12, "h": 1},
                {"id": "rx_tbl", "type": "table", "x": 0, "y": 3, "w": 12, "h": 6, "iterator": "medications"},
            ],
        }),
        "css_styles": "",
        "header_html": "<h1>{{ tenant.hospital_name }} ({{ tenant.branch_code }})</h1><h2>MEDICAL PRESCRIPTION (eRx #{{ prescription.id }})</h2>",
        "html_template": """
            <p><strong>Patient:</strong> {{ patient.full_name }} (MRN: {{ patient.mrn }})</p>
            <p><strong>Prescribing Doctor:</strong> {{ doctor.full_name }} | <strong>Reg No:</strong> {{ doctor.nmc_reg }}</p>
            <div style="color: red; font-weight: bold;">ALLERGY ALERT: {{ allergy_banner }}</div>
            <table>
              <thead><tr><th>Medication</th><th>Dosage Instruction</th><th>Qty</th></tr></thead>
              <tbody>
                {% for med in medications %}
                <tr>
                  <td>{{ med.brand_name }} ({{ med.generic_name }} {{ med.strength }})</td>
                  <td>{{ med.dosage_instruction }}</td>
                  <td>{{ med.requested_quantity }}</td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
            <p><strong>Clinical Notes:</strong> {{ prescription.clinical_notes }}</p>
        """,
        "footer_html": "<p style='text-align:right;margin-top:30px;'><strong>Doctor Signature: {{ doctor.full_name }} ({{ doctor.nmc_reg }})</strong></p>",
    },

    # 5. Verified Diagnostic Lab & Radiology Report
    {
        "report_type": ReportTypeEnum.DIAGNOSTIC_REPORT,
        "code": "SYS_DIAG_REPORT_A4",
        "name": "Standard A4 Verified Diagnostic Lab & Radiology Report",
        "description": "Official pathology & radiology report with dual technician/pathologist verification sign-off.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [
                {"id": "hdr", "type": "header", "x": 0, "y": 0, "w": 12, "h": 2},
                {"id": "lab_tbl", "type": "table", "x": 0, "y": 3, "w": 12, "h": 7, "iterator": "tasks"},
                {"id": "sign_dual", "type": "signatories", "x": 0, "y": 10, "w": 12, "h": 2},
            ],
        }),
        "css_styles": "",
        "header_html": "<h1>{{ tenant.hospital_name }} ({{ tenant.branch_code }})</h1><h2>VERIFIED DIAGNOSTIC LAB REPORT (Order #{{ order.id }})</h2>",
        "html_template": """
            <p><strong>Patient:</strong> {{ patient.full_name }} (MRN: {{ patient.mrn }}) | <strong>Gender:</strong> {{ patient.gender }}</p>
            <p><strong>Ordering Doctor:</strong> {{ order.ordering_doctor_name }} | <strong>Clinical Notes:</strong> {{ order.clinical_notes }}</p>
            <table>
              <thead><tr><th>Test</th><th>Result</th><th>Notes / Remarks</th><th>Performed By</th><th>Verified By</th></tr></thead>
              <tbody>
                {% for task in tasks %}
                <tr>
                  <td>{{ task.service_code }} - {{ task.service_name }}</td>
                  <td><strong>{{ task.result_value }}</strong></td>
                  <td>{{ task.result_notes }}</td>
                  <td>{{ task.performed_by_name }}</td>
                  <td>{{ task.verified_by_name }}</td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
        """,
        "footer_html": "<p style='margin-top:25px;'><strong>Electronically Verified Diagnostic Report — End of Report</strong></p>",
    },

    # 6. IPD Clinical Discharge Summary
    {
        "report_type": ReportTypeEnum.DISCHARGE_SUMMARY,
        "code": "SYS_IPD_DISCHARGE_A4",
        "name": "Standard A4 Inpatient Clinical Discharge Summary",
        "description": "Comprehensive IPD discharge summary with ICD-10 diagnoses, signed SOAP notes, and follow-up instructions.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [
                {"id": "hdr", "type": "header", "x": 0, "y": 0, "w": 12, "h": 2},
                {"id": "icd_list", "type": "list", "x": 0, "y": 3, "w": 12, "h": 3},
                {"id": "soap_block", "type": "notes", "x": 0, "y": 6, "w": 12, "h": 4},
            ],
        }),
        "css_styles": "",
        "header_html": "<h1>{{ tenant.hospital_name }} ({{ tenant.branch_code }})</h1><h2>INPATIENT CLINICAL DISCHARGE SUMMARY</h2>",
        "html_template": """
            <p><strong>Patient:</strong> {{ patient.full_name }} (MRN: {{ patient.mrn }})</p>
            <p><strong>Admitting Doctor:</strong> {{ admission.admitting_doctor_name }}</p>
            <p><strong>Discharged At:</strong> {{ admission.discharge_time }} | <strong>Disposition:</strong> {{ admission.discharge_disposition }}</p>
            <h3>Diagnoses (ICD-10)</h3>
            <ul>
              {% for d in diagnoses %}
              <li><strong>{{ d.diagnosis_type }}:</strong> {{ d.icd10_code }} - {{ d.short_description }}</li>
              {% else %}
              <li>No ICD-10 diagnoses recorded</li>
              {% endfor %}
            </ul>
            <h3>Signed Clinical Notes</h3>
            {% for n in signed_notes %}
            <div>
              <p><strong>Assessment:</strong> {{ n.assessment_notes }}</p>
              <p><strong>Plan:</strong> {{ n.plan_notes }}</p>
            </div>
            {% endfor %}
            <h3>Discharge Summary &amp; Instructions</h3>
            <p>{{ admission.discharge_summary }}</p>
        """,
        "footer_html": "<p style='margin-top:25px;'><strong>Attending Physician Signature: {{ admission.admitting_doctor_name }}</strong></p>",
    },
    # 7. Medico-Legal Case (MLC) Police Intimation Certificate
    {
        "report_type": ReportTypeEnum.MLC_POLICE_REPORT,
        "code": "SYS_MLC_POLICE_INTIMATION_A4",
        "name": "Standard A4 Medico-Legal Case (MLC) Police Intimation Report",
        "description": "Official forensic & casualty police intimation form for RTA, assault, poisoning, and trauma cases.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [{"id": "mlc_hdr", "type": "header", "x": 0, "y": 0, "w": 12, "h": 2}],
        }),
        "css_styles": "",
        "header_html": "<h1>{{ tenant.hospital_name }} ({{ tenant.branch_code }})</h1><h2>MEDICO-LEGAL CASE (MLC) POLICE INTIMATION CERTIFICATE — {{ mlc.mlc_number }}</h2>",
        "html_template": """
            <p><strong>To: The Station House Officer (SHO),</strong> {{ mlc.police_station_name }}</p>
            <table>
              <tr><td><strong>Patient Name:</strong> {{ patient.full_name }}</td><td><strong>MRN:</strong> {{ patient.mrn }}</td><td><strong>Gender:</strong> {{ patient.gender }}</td></tr>
              <tr><td><strong>Cause of Injury:</strong> {{ mlc.cause_of_injury }}</td><td><strong>Brought By:</strong> {{ mlc.brought_by_name }} ({{ mlc.brought_by_relation }})</td><td><strong>Constable Buckle No:</strong> {{ mlc.police_constable_buckle_no }}</td></tr>
            </table>
            <p><strong>Identification Marks:</strong> {{ mlc.identification_marks }}</p>
            <p><strong>Alleged History of Injury:</strong> {{ mlc.alleged_history }}</p>
            <p><strong>FIR Number:</strong> {{ mlc.fir_number }} | <strong>Intimated At:</strong> {{ mlc.police_intimated_at }}</p>
        """,
        "footer_html": "<p style='margin-top:30px;'><strong>Casualty Medical Officer: {{ mlc.doctor_name }}</strong> &nbsp;&nbsp;|&nbsp;&nbsp; <strong>Receiving Police Officer Signature: ____________________</strong></p>",
    },
    # 8. Surgical & Anesthesia Operative Note Report
    {
        "report_type": ReportTypeEnum.SURGICAL_OPERATIVE_NOTE,
        "code": "SYS_OT_OPERATIVE_NOTE_A4",
        "name": "Standard A4 Surgical & Anesthesia Operative Record",
        "description": "Official post-operative surgical findings, WHO checklist verification, anesthesia log, and implant serial registry.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [{"id": "ot_hdr", "type": "header", "x": 0, "y": 0, "w": 12, "h": 2}],
        }),
        "css_styles": "",
        "header_html": "<h1>{{ tenant.hospital_name }} ({{ tenant.branch_code }})</h1><h2>SURGICAL &amp; ANESTHESIA OPERATIVE RECORD — {{ surgery.case_number }}</h2>",
        "html_template": """
            <p><strong>Patient:</strong> {{ patient.full_name }} (MRN: {{ patient.mrn }}) | <strong>Operation Theatre:</strong> {{ surgery.theatre_name }}</p>
            <p><strong>Procedure:</strong> {{ surgery.procedure_name }} | <strong>Lead Surgeon:</strong> {{ surgery.surgeon_name }}</p>
            <p><strong>Anesthesia Type:</strong> {{ record.anesthesia_type }} (ASA Grade {{ record.asa_physical_status }}) | <strong>Est. Blood Loss:</strong> {{ record.estimated_blood_loss_ml }} mL</p>
            <h3>Operative Findings &amp; Procedure Details</h3>
            <p>{{ record.operative_findings }}</p>
            <h3>Post-Operative Instructions</h3>
            <p>{{ record.post_op_instructions }}</p>
            <h3>Implant &amp; Prosthetic Serial Registry</h3>
            <ul>
              {% for imp in implants %}
              <li><strong>{{ imp.implant_name }}</strong> ({{ imp.manufacturer }}) — S/N: {{ imp.serial_or_batch_number }} (Qty: {{ imp.quantity }})</li>
              {% else %}
              <li>No surgical implants recorded</li>
              {% endfor %}
            </ul>
        """,
        "footer_html": "<p style='margin-top:30px;'><strong>Lead Surgeon Signature: {{ surgery.surgeon_name }}</strong></p>",
    },
    # 9. Official Maternity Birth Certificate
    {
        "report_type": ReportTypeEnum.BIRTH_CERTIFICATE,
        "code": "SYS_BIRTH_CERTIFICATE_A4",
        "name": "Standard A4 Official Hospital Birth Certificate",
        "description": "Municipal & NABH birth certificate with newborn MRN, APGAR scores, birth weight, and mother details.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [{"id": "bc_hdr", "type": "header", "x": 0, "y": 0, "w": 12, "h": 2}],
        }),
        "css_styles": "",
        "header_html": "<h1>{{ tenant.hospital_name }} ({{ tenant.branch_code }})</h1><h2>OFFICIAL HOSPITAL BIRTH RECORD — {{ birth.certificate_number }}</h2>",
        "html_template": """
            <p><strong>Mother Name:</strong> {{ mother.full_name }} (MRN: {{ mother.mrn }})</p>
            <p><strong>Newborn Name:</strong> {{ birth.baby_full_name }} | <strong>Newborn MRN:</strong> {{ birth.newborn_mrn }}</p>
            <p><strong>Gender:</strong> {{ birth.baby_gender }} | <strong>Time of Birth:</strong> {{ birth.time_of_birth }}</p>
            <p><strong>Delivery Mode:</strong> {{ birth.delivery_mode }} | <strong>Birth Weight:</strong> {{ birth.birth_weight_kg }} kg | <strong>APGAR (1m/5m):</strong> {{ birth.apgar_1min }}/10 &amp; {{ birth.apgar_5min }}/10</p>
        """,
        "footer_html": "<p style='margin-top:30px;'><strong>Attending Obstetrician: {{ birth.doctor_name }}</strong></p>",
    },
    # 10. Form 4 Medical Certificate of Cause of Death (MCCD)
    {
        "report_type": ReportTypeEnum.DEATH_CERTIFICATE_MCCD,
        "code": "SYS_MCCD_DEATH_CERTIFICATE_A4",
        "name": "Standard A4 Form 4 Medical Certificate of Cause of Death (MCCD)",
        "description": "Statutory WHO/Municipal Form 4 Medical Certificate of Cause of Death with ICD-10 coding.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [{"id": "dc_hdr", "type": "header", "x": 0, "y": 0, "w": 12, "h": 2}],
        }),
        "css_styles": "",
        "header_html": "<h1>{{ tenant.hospital_name }} ({{ tenant.branch_code }})</h1><h2>FORM 4: MEDICAL CERTIFICATE OF CAUSE OF DEATH (MCCD) — {{ death.certificate_number }}</h2>",
        "html_template": """
            <p><strong>Deceased Patient:</strong> {{ patient.full_name }} (MRN: {{ patient.mrn }}, Gender: {{ patient.gender }})</p>
            <p><strong>Time of Death:</strong> {{ death.time_of_death }} | <strong>Manner of Death:</strong> {{ death.manner_of_death }}</p>
            <p><strong>Immediate Cause of Death (ICD-10):</strong> {{ death.icd10_code }} — {{ death.diagnosis_description }}</p>
            <p><strong>Antecedent / Underlying Causes:</strong> {{ death.antecedent_cause_notes }}</p>
            <p><strong>Autopsy Advised:</strong> {{ death.is_autopsy_advised }} | <strong>Body Handed Over To:</strong> {{ death.body_handed_over_to }}</p>
        """,
        "footer_html": "<p style='margin-top:30px;'><strong>Certifying Medical Practitioner: {{ death.doctor_name }}</strong></p>",
    },
    {
        "report_type": ReportTypeEnum.INSURANCE_PREAUTH,
        "code": "SYS_INSURANCE_PREAUTH_A4",
        "name": "Configurable Insurance Pre-Authorization Summary",
        "description": "Clinical and policy summary with a payer-specific document checklist; attach any insurer-mandated form.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [
                {"id": "patient", "type": "box", "x": 0, "y": 0, "w": 12, "h": 3},
                {"id": "clinical", "type": "box", "x": 0, "y": 3, "w": 12, "h": 4},
                {"id": "documents", "type": "table", "x": 0, "y": 8, "w": 12, "h": 6},
            ],
        }),
        "css_styles": "",
        "header_html": "<h1>{{ tenant.hospital_name }} — {{ tenant.branch_code }}</h1><h2>PRE-AUTHORIZATION SUPPORTING SUMMARY</h2>",
        "html_template": """
            <p><strong>Pre-Auth No:</strong> {{ preauth.preauth_number }} | <strong>Status:</strong> {{ preauth.status }} | <strong>Printed:</strong> {{ tenant.printed_at }}</p>
            <h3>Patient &amp; Coverage</h3>
            <p>{{ patient.full_name }} (MRN {{ patient.mrn }}, DOB {{ patient.date_of_birth }}, {{ patient.gender }})</p>
            <p><strong>Payer:</strong> {{ provider.name }} ({{ provider.code }}) | <strong>Plan:</strong> {{ policy.plan_name }}</p>
            <p><strong>Policy / Member:</strong> {{ policy.policy_number }} / {{ policy.member_id }} | <strong>Valid:</strong> {{ policy.valid_from }} to {{ policy.valid_till }}</p>
            <p><strong>Sum insured:</strong> {{ policy.sum_insured }}</p>
            <h3>Clinical Request</h3>
            <p><strong>Diagnosis:</strong> {{ diagnosis.code }} — {{ diagnosis.description }}</p>
            <p><strong>Requested:</strong> {{ preauth.requested_amount }} | <strong>Approved:</strong> {{ preauth.approved_amount }}</p>
            <p><strong>Clinical justification:</strong> {{ preauth.clinical_justification }}</p>
            <p><strong>Payer reference:</strong> {{ preauth.tpa_reference_number }} | <strong>Query:</strong> {{ preauth.query_remarks }}</p>
            <h3>Provider-Configured Submission Checklist</h3>
            <table><thead><tr><th>Required document</th><th>Included</th><th>Reviewer</th></tr></thead><tbody>
            {% for document in required_documents %}<tr><td>{{ document }}</td><td>[ ]</td><td></td></tr>{% endfor %}
            </tbody></table>
            <p><strong>Submission note:</strong> This is a supporting summary, not a substitute for the payer's prescribed form or portal workflow.</p>
        """,
        "footer_html": "<p>Prepared by: ____________________ &nbsp; Authorized signatory: ____________________ &nbsp; Date: ____________________</p>",
    },
    {
        "report_type": ReportTypeEnum.INSURANCE_CLAIM,
        "code": "SYS_INSURANCE_CLAIM_A4",
        "name": "Configurable Insurance Claim Submission Summary",
        "description": "Claim, final invoice, itemized charges, and payer-configured document checklist.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": json.dumps({
            "canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12},
            "widgets": [
                {"id": "claim", "type": "box", "x": 0, "y": 0, "w": 12, "h": 4},
                {"id": "charges", "type": "table", "x": 0, "y": 5, "w": 12, "h": 7},
                {"id": "documents", "type": "table", "x": 0, "y": 13, "w": 12, "h": 5},
            ],
        }),
        "css_styles": "",
        "header_html": "<h1>{{ tenant.hospital_name }} — {{ tenant.branch_code }}</h1><h2>INSURANCE CLAIM SUPPORTING SUMMARY</h2>",
        "html_template": """
            <p><strong>Claim:</strong> {{ claim.claim_number }} | <strong>Status:</strong> {{ claim.status }} | <strong>Printed:</strong> {{ tenant.printed_at }}</p>
            <p><strong>Payer:</strong> {{ provider.name }} ({{ provider.code }}) | <strong>Policy:</strong> {{ policy.policy_number }} | <strong>Member:</strong> {{ policy.member_id }}</p>
            <p><strong>Patient:</strong> {{ patient.full_name }} (MRN {{ patient.mrn }}, DOB {{ patient.date_of_birth }})</p>
            <p><strong>Pre-authorization:</strong> {{ preauth.preauth_number }} ({{ preauth.status }}) | <strong>Invoice:</strong> {{ invoice.invoice_number }} ({{ invoice.status }})</p>
            <table><thead><tr><th>Description</th><th>Qty</th><th>Unit</th><th>Total</th></tr></thead><tbody>
            {% for item in line_items %}<tr><td>{{ item.description }}</td><td>{{ item.quantity }}</td><td>{{ item.unit_price }}</td><td>{{ item.total_price }}</td></tr>{% endfor %}
            </tbody></table>
            <p><strong>Invoice gross:</strong> {{ invoice.gross_amount }} | <strong>Discount:</strong> {{ invoice.discount }} | <strong>Net:</strong> {{ invoice.net_amount }} ({{ invoice.currency_code }})</p>
            <p><strong>Paid:</strong> {{ invoice.amount_paid }} | <strong>Balance:</strong> {{ invoice.balance_due }}</p>
            <p><strong>Claimed:</strong> {{ claim.claimed_amount }} | <strong>Insurer payable:</strong> {{ claim.insurer_payable }} | <strong>Patient co-pay:</strong> {{ claim.patient_copay }} | <strong>Settled:</strong> {{ claim.settled_amount }}</p>
            <p><strong>Settlement reference:</strong> {{ claim.settlement_reference }} | <strong>Remarks:</strong> {{ claim.remarks }}</p>
            <h3>Provider-Configured Submission Checklist</h3>
            <table><thead><tr><th>Required document</th><th>Included</th><th>Reviewer</th></tr></thead><tbody>
            {% for document in required_documents %}<tr><td>{{ document }}</td><td>[ ]</td><td></td></tr>{% endfor %}
            </tbody></table>
            <p><strong>Submission note:</strong> Attach the payer's prescribed forms and source records; confirm requirements with the payer before submission.</p>
        """,
        "footer_html": "<p>Prepared by: ____________________ &nbsp; Authorized signatory: ____________________ &nbsp; Date: ____________________</p>",
    },
    {
        "report_type": ReportTypeEnum.PURCHASE_ORDER,
        "code": "SYS_PO_STANDARD_A4",
        "name": "Standard A4 Purchase Order",
        "description": "Official Purchase Order with vendor details and itemized costs.",
        "page_size": PageSizeEnum.A4_PORTRAIT,
        "is_default": True,
        "layout_schema": '{"canvas": {"width_mm": 210, "height_mm": 297, "grid_columns": 12}, "widgets": []}',
        "css_styles": "table { width: 100%; border-collapse: collapse; } th, td { border: 1px solid #ccc; padding: 8px; } th { background: #f4f4f4; text-align: left; }",
        "header_html": """
            <h1>{{ tenant.document_profile.legal_entity_name or tenant.hospital_name }}</h1>
            <p>Branch: {{ tenant.branch_code }}</p>
            <h2>PURCHASE ORDER: {{ po.po_number }}</h2>
        """,
        "html_template": """
            <div style="margin-bottom: 20px;">
                <strong>To Vendor:</strong><br/>
                {{ vendor.name }} ({{ vendor.code }})<br/>
                {{ vendor.address }}<br/>
                GSTIN: {{ vendor.gstin }}<br/>
                Attn: {{ vendor.contact_person }} ({{ vendor.phone }})
            </div>
            <div style="margin-bottom: 20px;">
                <strong>Delivery Due:</strong> {{ po.delivery_due_date }}<br/>
                <strong>Payment Terms:</strong> {{ po.payment_terms }}
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Description</th>
                        <th>Qty Ordered</th>
                        <th>Unit Price</th>
                        <th>Total Price</th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in items %}
                    <tr>
                        <td>{{ item.description }}</td>
                        <td>{{ item.quantity_ordered }}</td>
                        <td>{{ item.unit_price }}</td>
                        <td>{{ item.total_price }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            <h3 style="text-align: right; margin-top: 20px;">Total Amount: {{ po.total_amount }}</h3>
        """,
        "footer_html": "<p style='text-align:center; font-size: 10px;'>Authorized System Generated Purchase Order</p>"
    },
]