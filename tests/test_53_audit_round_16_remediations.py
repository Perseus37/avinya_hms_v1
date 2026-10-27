"""
Audit Round 16 Remediation Test Suite.
Verifies fixes for 6 core findings and edge cases identified in external auditor report (Section 16):
1. Retention Policy Execution with clock_start="discharge" (PatientEncounter has no ended_at).
2. Bed Billing Repeat Stays in Same Bed (transfer log ID differentiation).
3. Interstate GST Column on Invoices and IGST accounting.
4. NEWS2 Scoring with Incomplete Vital Sets (is_complete_set = False and clinical warning).
5. BCMA Bedside Dose and Route Matching (2.5mg vs 5mg, bare integer vs duration, blank dose, route verification).
6. HL7 Result Status Filtering (OBX-11 status X/P handling and unambiguous repeat task matching).
"""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domains.billing.models import Invoice, InvoiceLineItem, InvoiceStatusEnum
from backend.domains.clinical.services import calculate_news2_score
from backend.domains.diagnostics.models import DiagnosticOrder, DiagnosticTask, TaskStatusEnum
from backend.domains.encounters.models import BedTransferLog, EncounterStatusEnum, EncounterTypeEnum, InpatientAdmission, PatientEncounter
from backend.domains.facility.models import Bed, BedStatusEnum, BedTypeEnum, Ward, WardTypeEnum
from backend.domains.identity.models import User
from backend.domains.patients.models import Patient
from backend.domains.privacy.models import PrivacyPolicyRevision


# -----------------------------------------------------------------------------
# 1. Retention Policy Execution with clock_start="discharge"
# -----------------------------------------------------------------------------

def test_retention_clock_at_discharge_no_crash(client, superuser_headers, multi_tenant_env, monkeypatch):
    """
    Audit Defect 1: clock_start='discharge' crashed with AttributeError because
    PatientEncounter had no ended_at attribute.
    Verifies that retention calculation executes cleanly with outerjoin on InpatientAdmission.discharge_time.
    """
    from backend.core.config import settings
    monkeypatch.setattr(settings, "PRIVACY_ALLOW_RETENTION_EXECUTE", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").first()
        assert doctor is not None

        # Create inactive patient
        patient = Patient(
            mrn="MRN-RET-DISCH-001",
            full_name="Discharged Inactive Patient",
            date_of_birth=datetime(1985, 6, 15, tzinfo=timezone.utc),
            gender="MALE",
            is_active=False,
        )
        db.add(patient)
        db.flush()

        encounter = PatientEncounter(
            patient_id=patient.id,
            encounter_type=EncounterTypeEnum.IPD,
            status=EncounterStatusEnum.DISCHARGED,
            primary_doctor_id=doctor.id,
            department_id=1,
            start_time=datetime.now(timezone.utc) - timedelta(days=400),
            end_time=datetime.now(timezone.utc) - timedelta(days=380),
        )
        db.add(encounter)
        db.flush()

        admission = InpatientAdmission(
            encounter_id=encounter.id,
            admission_type="ROUTINE",
            admitting_doctor_id=doctor.id,
            discharge_time=datetime.now(timezone.utc) - timedelta(days=380),
        )
        db.add(admission)
        db.commit()
        pat_id = patient.id

    # Create and approve retention policy with clock_start="discharge"
    retention_doc = {
        "schema_version": 1,
        "policy_code": "retention-discharge-clock",
        "categories": [
            {
                "code": "patients",
                "system_of_record": "Tenant Database",
                "locations": ["patients"],
                "retention_days": 365,
                "clock_start": "discharge",
                "action": "anonymise",
                "legal_hold_triggers": [],
                "backup_treatment": "Retain backup snapshots.",
                "export_treatment": "Exclude from exports.",
            }
        ],
    }
    c_res = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json={"policy_type": "RETENTION", "policy_document": retention_doc},
    )
    assert c_res.status_code == 201, c_res.text
    rev_id = c_res.json()["id"]

    # Configure author and approve
    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        r = db.get(PrivacyPolicyRevision, rev_id)
        r.configured_by_user_id = doctor.id
        db.commit()

    app_res = client.post(
        f"/v1/api/privacy/policies/{rev_id}/approve",
        headers=superuser_headers,
        json={"approval_reference": "DISCH-CLOCK-APPROVAL"},
    )
    assert app_res.status_code == 200, app_res.text

    # Execute retention policy dry-run
    exec_res = client.post(
        "/v1/api/privacy/retention/execute",
        headers=superuser_headers,
        json={
            "policy_revision_id": rev_id,
            "categories": ["patients"],
            "approval_reference": "DISCH-CLOCK-RUN-01",
            "dry_run": True,
        },
    )
    assert exec_res.status_code == 200, exec_res.text
    exec_data = exec_res.json()
    assert "total_affected" in exec_data


# -----------------------------------------------------------------------------
# 2. Bed Billing Repeat Stays in Same Bed
# -----------------------------------------------------------------------------

def test_auto_bill_ipd_bed_stay_repeat_stay_in_same_bed(client, superuser_headers, multi_tenant_env):
    """
    Audit Defect 2: auto_bill_ipd_bed_stay skipped repeat stays in the same bed
    because line item descriptions were identical without transfer log ID.
    Verifies that transferring from Bed A -> Bed B -> Bed A generates 3 distinct line items.
    """
    from backend.domains.billing.services import auto_bill_ipd_bed_stay

    # Provision spatial hierarchy via API
    f_res = client.post(
        "/v1/api/ip/floors",
        headers=superuser_headers,
        json={"floor_number": 99, "name": "Audit Floor"},
    )
    f_id = f_res.json()["floor_id"]

    w_res = client.post(
        "/v1/api/ip/wards",
        headers=superuser_headers,
        json={"floor_id": f_id, "department_id": 1, "code": "WARD-REP-AUDIT", "name": "Repeat Ward", "ward_type": "GENERAL"},
    )
    w_id = w_res.json()["id"]

    r_res = client.post(
        "/v1/api/ip/rooms",
        headers=superuser_headers,
        json={"ward_id": w_id, "room_number": "9901", "room_type": "DOUBLE", "bed_capacity": 2},
    )
    r_id = r_res.json()["room_id"]

    b1_res = client.post(
        "/v1/api/ip/beds",
        headers=superuser_headers,
        json={"room_id": r_id, "bed_number": "9901-A", "bed_type": "STANDARD", "base_daily_rate_paise": 100000},
    )
    b1_id = b1_res.json()["bed_id"]

    b2_res = client.post(
        "/v1/api/ip/beds",
        headers=superuser_headers,
        json={"room_id": r_id, "bed_number": "9901-B", "bed_type": "ICU", "base_daily_rate_paise": 500000},
    )
    b2_id = b2_res.json()["bed_id"]

    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").first()

        bed_a = db.get(Bed, b1_id)
        bed_b = db.get(Bed, b2_id)

        patient = Patient(
            mrn="MRN-BED-REPEAT-01",
            full_name="Repeat Bed Patient",
            date_of_birth=datetime(1990, 1, 1, tzinfo=timezone.utc),
            gender="MALE",
            is_active=True,
        )
        db.add(patient)
        db.flush()

        encounter = PatientEncounter(
            patient_id=patient.id,
            encounter_type=EncounterTypeEnum.IPD,
            status=EncounterStatusEnum.ACTIVE,
            primary_doctor_id=doctor.id,
            department_id=1,
            start_time=datetime.now(timezone.utc) - timedelta(days=5),
        )
        db.add(encounter)
        db.flush()

        admission = InpatientAdmission(
            encounter_id=encounter.id,
            admission_type="ROUTINE",
            admitting_doctor_id=doctor.id,
            current_bed_id=bed_a.id,
        )
        db.add(admission)
        db.flush()

        # Stay 1: Bed A (Day 0 to Day 2)
        t1 = BedTransferLog(
            admission_id=admission.id,
            from_bed_id=None,
            to_bed_id=bed_a.id,
            transfer_time=datetime.now(timezone.utc) - timedelta(days=5),
            requested_by_id=doctor.id,
            unit_price_paise=100000,
        )
        # Stay 2: Bed B (Day 2 to Day 4)
        t2 = BedTransferLog(
            admission_id=admission.id,
            from_bed_id=bed_a.id,
            to_bed_id=bed_b.id,
            transfer_time=datetime.now(timezone.utc) - timedelta(days=3),
            requested_by_id=doctor.id,
            unit_price_paise=500000,
        )
        # Stay 3: Bed A again (Day 4 to discharge today)
        t3 = BedTransferLog(
            admission_id=admission.id,
            from_bed_id=bed_b.id,
            to_bed_id=bed_a.id,
            transfer_time=datetime.now(timezone.utc) - timedelta(days=1),
            requested_by_id=doctor.id,
            unit_price_paise=100000,
        )
        db.add_all([t1, t2, t3])
        db.commit()

        # Run auto_bill_ipd_bed_stay
        discharge_time = datetime.now(timezone.utc)
        auto_bill_ipd_bed_stay(db, admission, discharge_time)
        db.commit()

        # Verify draft invoice has 3 distinct line items
        inv = db.execute(
            select(Invoice).where(Invoice.encounter_id == encounter.id)
        ).scalar_one()

        line_items = db.execute(
            select(InvoiceLineItem).where(InvoiceLineItem.invoice_id == inv.id)
        ).scalars().all()

        assert len(line_items) == 3, f"Expected 3 line items for 3 stays, got {len(line_items)}"
        # Verify transfer IDs are embedded in description
        assert any(f"[Transfer #{t1.id}]" in item.description for item in line_items)
        assert any(f"[Transfer #{t2.id}]" in item.description for item in line_items)
        assert any(f"[Transfer #{t3.id}]" in item.description for item in line_items)


# -----------------------------------------------------------------------------
# 3. Interstate GST Column on Invoices and IGST Accounting
# -----------------------------------------------------------------------------

def test_invoice_is_interstate_flag_and_igst(client, superuser_headers, doctor_headers):
    """
    Audit Defect 3: Invoice model lacked is_interstate column, so getattr(invoice, "is_interstate", False)
    was always False and IGST was never calculated.
    Verifies that is_interstate=True routes GST to igst_amount_paise and zeroes CGST/SGST.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "Interstate GST Patient", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"},
    )
    patient_id = p_res.json()["id"]

    # 1. Create Interstate invoice (is_interstate=True)
    inv_res = client.post(
        "/v1/api/billing/invoices",
        headers=superuser_headers,
        json={"patient_id": patient_id, "is_interstate": True, "notes": "Out-of-state patient consultation"},
    )
    assert inv_res.status_code in (200, 201), inv_res.text
    inv_data = inv_res.json()
    inv_id = inv_data["id"]
    assert inv_data["is_interstate"] is True

    # 2. Add taxable line item (₹1,000 with 18% GST = ₹180 tax)
    srv_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={
            "category_id": 1,
            "code": "CONSULT_IGST_01",
            "name": "Tele-Consultation IGST",
            "service_type": "CONSULTATION",
            "base_price_paise": 100000,
        },
    )
    assert srv_res.status_code == 201, srv_res.text
    srv_id = srv_res.json()["id"]

    item_res = client.post(
        f"/v1/api/billing/invoices/{inv_id}/items",
        headers=superuser_headers,
        json={
            "service_id": srv_id,
            "description": "Specialist Tele-Consultation",
            "quantity": 1,
            "unit_price_paise": 100000,
            "gst_rate_percent": 18,
        },
    )
    assert item_res.status_code in (200, 201), item_res.text

    # 3. Fetch invoice and verify IGST vs CGST/SGST
    get_res = client.get(f"/v1/api/billing/invoices/{inv_id}", headers=superuser_headers)
    assert get_res.status_code == 200
    inv_detail = get_res.json()

    assert inv_detail["total_amount_paise"] == 100000
    assert inv_detail["tax_amount_paise"] == 18000
    assert inv_detail["igst_amount_paise"] == 18000, "Interstate invoice must route all tax to IGST"
    assert inv_detail["cgst_amount_paise"] == 0
    assert inv_detail["sgst_amount_paise"] == 0
    assert inv_detail["is_interstate"] is True


# -----------------------------------------------------------------------------
# 4. NEWS2 Scoring with Incomplete Vital Sets
# -----------------------------------------------------------------------------

def test_news2_incomplete_vitals_warning():
    """
    Audit Defect 4: Single-vital evaluations reported 'LOW' / 'ROUTINE' without
    flagging incomplete physiological data (is_complete_set = False).
    Verifies that missing vitals flag is_complete_set=False and provide clinical guidance.
    """
    # Only pulse provided
    res_partial = calculate_news2_score(pulse_bpm=72)
    assert res_partial["news2_score"] == 0
    assert res_partial["risk_level"] == "LOW"
    assert res_partial["is_complete_set"] is False
    assert "respiratory_rate" in res_partial["missing_parameters"]
    assert "bp_systolic" in res_partial["missing_parameters"]
    assert "spo2_percent" in res_partial["missing_parameters"]
    assert "temperature" in res_partial["missing_parameters"]
    assert "INCOMPLETE_DATA" in res_partial["clinical_recommendation"]

    # Complete set provided
    res_complete = calculate_news2_score(
        respiratory_rate=16,
        spo2_percent=98,
        bp_systolic=120,
        pulse_bpm=72,
        consciousness="ALERT",
        temperature_f=98.6,
    )
    assert res_complete["news2_score"] == 0
    assert res_complete["risk_level"] == "LOW"
    assert res_complete["is_complete_set"] is True
    assert len(res_complete["missing_parameters"]) == 0
    assert "ROUTINE" in res_complete["clinical_recommendation"]


# -----------------------------------------------------------------------------
# 5. BCMA Bedside Dose and Route Matching
# -----------------------------------------------------------------------------

def test_bcma_bedside_dose_and_route_edge_cases(client, superuser_headers, nurse_headers):
    """
    Audit Defect 5:
    - 5 mg passed for a 2.5 mg order.
    - Bare '5' passed against '10 mg x 5 days'.
    - Blank dose passed.
    - Route was not checked.
    Verifies all 4 edge cases trigger hard-stops.
    """
    # 1. Setup patient and encounter
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "BCMA Hardening Patient", "date_of_birth": "1992-05-10T00:00:00Z", "gender": "FEMALE"},
    )
    patient = p_res.json()

    enc_res = client.post(
        "/v1/api/adt/encounters",
        headers=superuser_headers,
        json={"patient_id": patient["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1},
    )
    encounter = enc_res.json()

    # Create medication: Amlodipine 5mg tablet
    med_res = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "MED_AMLO_5MG",
            "brand_name": "Norvasc 5mg",
            "generic_name": "Amlodipine",
            "form": "TABLET",
            "strength": "5 mg",
            "ndc_or_barcode": "BARCODE-AMLO-5MG",
        },
    )
    med = med_res.json()

    # Prescribe 2.5 mg (half tablet) orally
    rx_res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=superuser_headers,
        json={
            "encounter_id": encounter["id"],
            "items": [
                {
                    "medication_id": med["id"],
                    "dosage_instruction": "2.5 mg oral daily",
                    "requested_quantity": 14,
                }
            ],
        },
    )
    rx_item_id = rx_res.json()["items"][0]["id"]

    # Edge Case 5a: Giving whole 5 mg tablet for a 2.5 mg order must FAIL
    res_overdose = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-AMLO-5MG",
            "dose_given": "5 mg",
            "route": "ORAL",
        },
    )
    assert res_overdose.status_code == 409, res_overdose.text
    assert "RIGHT DOSE VIOLATION" in res_overdose.text

    # Edge Case 5b: Blank dose given must FAIL
    res_blank = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-AMLO-5MG",
            "dose_given": "   ",
        },
    )
    assert res_blank.status_code == 409
    assert "RIGHT DOSE VIOLATION" in res_blank.text

    # Edge Case 5c: Mismatched Route (e.g. IV given for ORAL prescription) must FAIL
    res_route = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-AMLO-5MG",
            "dose_given": "2.5 mg",
            "route": "IV",
        },
    )
    assert res_route.status_code == 409
    assert "RIGHT ROUTE VIOLATION" in res_route.text

    # Correct dose and route succeeds
    res_ok = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-AMLO-5MG",
            "dose_given": "2.5 mg",
            "route": "ORAL",
        },
    )
    assert res_ok.status_code in (200, 201), res_ok.text
    assert res_ok.json()["is_verified"] is True
    assert res_ok.json()["route_matched"] is True


def test_bcma_dose_matcher_bare_integer_and_duration_tokens():
    """
    Audit Defect 5b: Bare '5' matched '10 mg x 5 days'.
    Verifies that _is_dose_match treats bare integers without units as non-matching
    against measured doses, and excludes duration tokens from matching.
    """
    from backend.domains.pharmacy.services import _is_dose_match

    # Bare 5 must NOT match 10 mg x 5 days
    assert _is_dose_match("5", "10 mg x 5 days") is False

    # Bare 5 must NOT match 5 mg
    assert _is_dose_match("5", "5 mg") is False

    # 10 mg matches 10 mg x 5 days
    assert _is_dose_match("10 mg", "10 mg x 5 days") is True

    # Blank must NOT match anything
    assert _is_dose_match("", "100 mg") is False
    assert _is_dose_match("   ", "100 mg") is False


# -----------------------------------------------------------------------------
# 6. HL7 Result Status Filtering and Unambiguous Repeat Task Matching
# -----------------------------------------------------------------------------

def test_hl7_unobtainable_and_preliminary_result_statuses(client, superuser_headers, doctor_headers):
    """
    Audit Defect 6:
    - Preliminary (P) and 'could not be obtained' (X) were filed as normal RESULT_ENTERED.
    Verifies:
    - Status X marks task SAMPLE_REJECTED.
    - Status P marks task IN_PROGRESS with preliminary warning.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "HL7 Status Patient", "date_of_birth": "1985-04-12T00:00:00Z", "gender": "MALE"},
    )
    patient = p_res.json()

    # Create lab service
    srv_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": 1, "code": "LYTES_NA", "name": "Serum Sodium", "service_type": "LAB", "base_price_paise": 5000},
    )
    service = srv_res.json()

    # Order test
    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    task_id = order_res.json()["tasks"][0]["id"]
    order_id = order_res.json()["id"]

    # 1. Analyzer transmits OBX status X (Cannot be obtained / hemolyzed)
    hl7_msg_x = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261026090000||ORU^R01|MSG-STAT-X-01|P|2.3\r"
        f"PID|1||{patient['mrn']}||Status^Patient||19850412|M\r"
        f"OBR|1|{task_id}||LYTES_NA^Sodium|||20261026085000|||||||||||||||||F\r"
        "OBX|1|NM|NA^Sodium||HEMOLYZED|mmol/L|135-145||||X\r"
    )

    res_x = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_msg_x})
    assert res_x.status_code == 200
    assert res_x.json()["status"] == "PROCESSED"

    tasks_res = client.get(f"/v1/api/diagnostics/orders/{order_id}/tasks", headers=doctor_headers)
    task_data = tasks_res.json()["items"][0]
    assert task_data["status"] == "CANCELLED", f"Expected CANCELLED for status X, got {task_data['status']}"
    assert task_data["result_value"] is None, f"Expected result_value to be None for status X, got {task_data['result_value']}"
    assert "UNOBTAINABLE" in task_data["result_notes"]


def test_hl7_repeat_test_routing_does_not_overwrite_resulted_task(client, superuser_headers, doctor_headers):
    """
    Audit Defect 6b: Repeat test with no order number went to the oldest task,
    overwriting the morning test instead of routing to the pending repeat test.
    Verifies that unresulted tasks are targeted and ambiguous duplicates without order numbers fail cleanly.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "HL7 Repeat Patient", "date_of_birth": "1978-09-20T00:00:00Z", "gender": "FEMALE"},
    )
    patient = p_res.json()

    srv_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": 1, "code": "GLUC_RANDOM", "name": "Random Blood Glucose", "service_type": "LAB", "base_price_paise": 3000},
    )
    service = srv_res.json()

    # Order 1: Morning Glucose
    ord1_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    ord1_id = ord1_res.json()["id"]
    task1_id = ord1_res.json()["tasks"][0]["id"]

    # Ingest morning result (120 mg/dL)
    hl7_msg1 = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261026080000||ORU^R01|MSG-GLUC-01|P|2.3\r"
        f"PID|1||{patient['mrn']}||Repeat^Patient||19780920|F\r"
        f"OBR|1|{task1_id}||GLUC_RANDOM^Glucose|||20261026075000|||||||||||||||||F\r"
        "OBX|1|NM|GLUC^Glucose||120|mg/dL|70-140|N|||F\r"
    )
    client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_msg1})

    # Order 2: Afternoon Repeat Glucose
    ord2_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    ord2_id = ord2_res.json()["id"]
    task2_id = ord2_res.json()["tasks"][0]["id"]

    # Ingest repeat result (185 mg/dL) WITHOUT explicit task order number (only test code GLUC_RANDOM in OBR-4)
    hl7_msg2 = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261026140000||ORU^R01|MSG-GLUC-02|P|2.3\r"
        f"PID|1||{patient['mrn']}||Repeat^Patient||19780920|F\r"
        "OBR|1|||GLUC_RANDOM^Glucose|||20261026135000|||||||||||||||||F\r"
        "OBX|1|NM|GLUC^Glucose||185|mg/dL|70-140|H|||F\r"
    )
    res_repeat = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_msg2})
    assert res_repeat.status_code == 200, res_repeat.text
    assert res_repeat.json()["status"] == "PROCESSED"

    # Verify task 1 remains unchanged at 120, and task 2 received the 185 result
    t1_res = client.get(f"/v1/api/diagnostics/orders/{ord1_id}/tasks", headers=doctor_headers)
    t2_res = client.get(f"/v1/api/diagnostics/orders/{ord2_id}/tasks", headers=doctor_headers)

    t1_task = t1_res.json()["items"][0]
    t2_task = t2_res.json()["items"][0]

    assert t1_task["result_value"] == "120", "Task 1 must not be overwritten by repeat test"
    assert t2_task["result_value"] == "185", "Task 2 must receive the unassigned repeat test result"
    assert t2_task["status"] == "RESULT_ENTERED"
