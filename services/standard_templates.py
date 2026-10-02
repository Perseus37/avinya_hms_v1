import json
from typing import Any, Dict, List
from fastapi import HTTPException, status
from jinja2.exceptions import SecurityError, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from models import PageSizeEnum, ReportTypeEnum

# Strict Sandboxed Jinja2 Environment (blocks SSTI / arbitrary Python attribute access)
_sandbox_env = SandboxedEnvironment(autoescape=True)


def render_sandboxed_template(
    html_template: str,
    css_styles: str | None,
    header_html: str | None,
    footer_html: str | None,
    context: Dict[str, Any],
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

    css_block = css_styles or ""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: Arial, sans-serif; color: #1e293b; margin: 20px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
    th, td {{ border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; }}
    th {{ background-color: #f8fafc; }}
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
            "group_name": "Invoice Totals & Status",
            "variables": [
                {"label": "Invoice Number", "token": "{{ invoice.invoice_number }}", "sample": "INV-20261002-A1B2"},
                {"label": "Invoice Status", "token": "{{ invoice.status }}", "sample": "PAID"},
                {"label": "Total Amount (₹)", "token": "{{ invoice.total_inr }}", "sample": "₹500.00"},
                {"label": "Amount Paid (₹)", "token": "{{ invoice.paid_inr }}", "sample": "₹500.00"},
                {"label": "Balance Due (₹)", "token": "{{ invoice.balance_inr }}", "sample": "₹0.00"},
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
                {"label": "Clinical Notes", "token": "{{ order.clinical_notes }}", "sample": "Rule out anemia"},
            ],
        },
        {
            "group_name": "Verified Lab & Radiology Tasks",
            "is_table": True,
            "iterator": "tasks",
            "columns": [
                {"label": "Test Code & Name", "token": "{{ task.service_code }} - {{ task.service_name }}"},
                {"label": "Result Value", "token": "{{ task.result_value }}"},
                {"label": "Reference / Notes", "token": "{{ task.result_notes }}"},
                {"label": "Performed By", "token": "{{ task.performed_by_name }}"},
                {"label": "Verified By", "token": "{{ task.verified_by_name }}"},
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
        "header_html": "<h1>{{ tenant.hospital_name }} ({{ tenant.branch_code }})</h1><h2>TAX INVOICE &amp; PAYMENT RECEIPT — {{ invoice.invoice_number }}</h2>",
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
            <p><strong>Total Amount:</strong> {{ invoice.total_inr }}</p>
            <p><strong>Amount Paid:</strong> {{ invoice.paid_inr }}</p>
            <p><strong>Balance Due:</strong> {{ invoice.balance_inr }}</p>
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
]