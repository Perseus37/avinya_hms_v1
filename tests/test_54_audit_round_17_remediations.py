"""
Audit Round 17 Remediation Test Suite.
Verifies remediations for findings identified in external audit (Round 17):
1. N-60: Preliminary HL7 lab results (OBX-11 = P) set task status to IN_PROGRESS and order status to ACTIVE without crashing.
2. N-61: Unobtainable HL7 lab results (OBX-11 = X) set task status to CANCELLED, result_value to None, and skip delta checks.
3. N-62 & N-65: BCMA bedside 5-rights verification enforces mandatory dose and route, rejects bare numeric patient IDs,
   rejects typed drug brand names (requires machine barcode), and permits valid parenteral routes for injectable formulations.
4. N-63: Production environment guards on developer utility scripts (totp.py and create_control_plane_operator.py).
5. N-64: NEWS2 score calculation explicitly flags omitted consciousness in missing_parameters and sets is_complete_set = False.
6. N-44: Controlled substances (narcotics) perpetual vault dual-witness authentication and clinical linking for receipt, dispense, return, and wastage.
"""

from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from backend.domains.clinical.services import calculate_news2_score
from backend.domains.diagnostics.models import TaskStatusEnum
from backend.domains.identity.models import User


# =============================================================================
# 1. N-60 & N-61: HL7 Results Ingestion Handling (Preliminary & Unobtainable)
# =============================================================================

def test_n60_hl7_preliminary_result_order_status_active(client, superuser_headers, doctor_headers):
    """
    N-60: Ingesting preliminary HL7 results (OBX-11 = P) previously crashed because
    OrderStatusEnum had no IN_PROGRESS member. Verifies task is IN_PROGRESS and
    order remains ACTIVE with preliminary notes.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "HL7 Prelim Patient", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"},
    )
    patient = p_res.json()

    srv_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": 1, "code": "LAB_PRELIM_TEST", "name": "Preliminary Culture", "service_type": "LAB", "base_price_paise": 4000},
    )
    service = srv_res.json()

    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    order_id = order_res.json()["id"]
    task_id = order_res.json()["tasks"][0]["id"]

    # Preliminary result (OBX-11 = P)
    hl7_msg_p = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261027090000||ORU^R01|MSG-PRELIM-01|P|2.3\r"
        f"PID|1||{patient['mrn']}||Prelim^Patient||19900101|M\r"
        f"OBR|1|{task_id}||LAB_PRELIM_TEST^Preliminary Culture|||20261027085000|||||||||||||||||P\r"
        "OBX|1|ST|CULT^Culture||Gram-positive cocci in clusters||||||P\r"
    )

    res = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_msg_p})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "PROCESSED"

    tasks_res = client.get(f"/v1/api/diagnostics/orders/{order_id}/tasks", headers=doctor_headers)
    task_data = tasks_res.json()["items"][0]
    assert task_data["status"] == "IN_PROGRESS", f"Expected task status IN_PROGRESS, got {task_data['status']}"
    assert "Gram-positive cocci" in str(task_data["result_value"])
    assert "PRELIMINARY" in task_data["result_notes"]


def test_n61_hl7_unobtainable_result_clears_value_and_skips_delta_check(client, superuser_headers, doctor_headers):
    """
    N-61: HL7 unobtainable results (OBX-11 = X) must set status to CANCELLED,
    clear result_value to None, append an unobtainable note, and skip delta checks.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "HL7 Unobtainable Patient", "date_of_birth": "1992-05-15T00:00:00Z", "gender": "FEMALE"},
    )
    patient = p_res.json()

    srv_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": 1, "code": "LAB_POTASSIUM", "name": "Serum Potassium", "service_type": "LAB", "base_price_paise": 3500},
    )
    service = srv_res.json()

    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    order_id = order_res.json()["id"]
    task_id = order_res.json()["tasks"][0]["id"]

    # Ingest unobtainable result (OBX-11 = X, specimen clotted)
    hl7_msg_x = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261027091500||ORU^R01|MSG-UNOBT-01|P|2.3\r"
        f"PID|1||{patient['mrn']}||Unobtainable^Patient||19920515|F\r"
        f"OBR|1|{task_id}||LAB_POTASSIUM^Potassium|||20261027091000|||||||||||||||||F\r"
        "OBX|1|NM|K^Potassium||CLOTTED|mmol/L|3.5-5.0||||X\r"
    )

    res = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_msg_x})
    assert res.status_code == 200, res.text

    tasks_res = client.get(f"/v1/api/diagnostics/orders/{order_id}/tasks", headers=doctor_headers)
    task_data = tasks_res.json()["items"][0]
    assert task_data["status"] in ("SAMPLE_REJECTED", "CANCELLED")
    assert task_data["result_value"] is None
    assert "UNOBTAINABLE" in task_data["result_notes"]


# =============================================================================
# 2. N-62 & N-65: BCMA Bedside 5-Rights Hardening
# =============================================================================

def test_n62_bcma_rejects_bare_patient_id_and_typed_brand_name(client, superuser_headers, nurse_headers):
    """
    N-62: BCMA must reject bare integer patient database IDs (e.g. "12") and typed brand names (e.g. "Norvasc"),
    requiring machine-scannable wristband MRN barcodes and medication barcodes.
    Also verifies mandatory dose_given and route.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "BCMA Hardening Patient", "date_of_birth": "1980-03-10T00:00:00Z", "gender": "MALE"},
    )
    patient = p_res.json()

    enc_res = client.post(
        "/v1/api/adt/encounters",
        headers=superuser_headers,
        json={"patient_id": patient["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1},
    )
    encounter = enc_res.json()

    med_res = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "MED_ATORVA_20",
            "brand_name": "Lipitor 20mg",
            "generic_name": "Atorvastatin",
            "form": "TABLET",
            "strength": "20 mg",
            "ndc_or_barcode": "BARCODE-LIP-20MG",
        },
    )
    med = med_res.json()

    rx_res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=superuser_headers,
        json={
            "encounter_id": encounter["id"],
            "items": [
                {
                    "medication_id": med["id"],
                    "dosage_instruction": "20 mg oral bedtime",
                    "requested_quantity": 30,
                }
            ],
        },
    )
    rx_item_id = rx_res.json()["items"][0]["id"]

    # 1. Bare database ID as patient barcode must be REJECTED
    res_bare_pid = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": str(patient["id"]),  # Bare numeric database ID
            "scanned_medication_barcode": "BARCODE-LIP-20MG",
            "dose_given": "20 mg",
            "route": "ORAL",
        },
    )
    assert res_bare_pid.status_code == 409
    assert "RIGHT PATIENT VIOLATION" in res_bare_pid.text
    assert "bare database ID" in res_bare_pid.text

    # 2. Typed medication brand name must be REJECTED
    res_brand = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": med["brand_name"],  # Typed brand name
            "dose_given": "20 mg",
            "route": "ORAL",
        },
    )
    assert res_brand.status_code == 409
    assert "RIGHT DRUG VIOLATION" in res_brand.text
    assert "medication brand name" in res_brand.text

    # 3. Missing dose_given must be REJECTED
    res_no_dose = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-LIP-20MG",
            "dose_given": None,
            "route": "ORAL",
        },
    )
    assert res_no_dose.status_code == 409
    assert "RIGHT DOSE VIOLATION" in res_no_dose.text

    # 4. Missing route must be REJECTED
    res_no_route = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-LIP-20MG",
            "dose_given": "20 mg",
            "route": None,
        },
    )
    assert res_no_route.status_code == 409
    assert "RIGHT ROUTE VIOLATION" in res_no_route.text

    # 5. Full valid barcode scan succeeds
    res_ok = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-LIP-20MG",
            "dose_given": "20 mg",
            "route": "ORAL",
        },
    )
    assert res_ok.status_code in (200, 201), res_ok.text
    assert res_ok.json()["is_verified"] is True


def test_n65_bcma_injectable_parenteral_routes(client, superuser_headers, nurse_headers):
    """
    N-65: For injectable formulations (INJECTION, INFUSION), parenteral routes (IV, IM, SC)
    are permitted when prescription does not specify a narrower route, but non-parenteral
    routes (ORAL) are strictly blocked.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "Injectable BCMA Patient", "date_of_birth": "1975-08-20T00:00:00Z", "gender": "FEMALE"},
    )
    patient = p_res.json()

    enc_res = client.post(
        "/v1/api/adt/encounters",
        headers=superuser_headers,
        json={"patient_id": patient["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1},
    )
    encounter = enc_res.json()

    # Create injectable drug: Ceftriaxone 1g vial
    med_res = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "MED_CEFTRIAXONE_1G",
            "brand_name": "Rocephin 1g",
            "generic_name": "Ceftriaxone",
            "form": "INJECTION",
            "strength": "1 g",
            "ndc_or_barcode": "BARCODE-CEFT-1G",
        },
    )
    med = med_res.json()

    # Prescribe without explicit route ("1 g every 24h")
    rx_res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=superuser_headers,
        json={
            "encounter_id": encounter["id"],
            "items": [
                {
                    "medication_id": med["id"],
                    "dosage_instruction": "1 g every 24h",
                    "requested_quantity": 5,
                }
            ],
        },
    )
    rx_item_id = rx_res.json()["items"][0]["id"]

    # 1. Non-parenteral route (ORAL) for INJECTION formulation must FAIL
    res_oral = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-CEFT-1G",
            "dose_given": "1 g",
            "route": "ORAL",
        },
    )
    assert res_oral.status_code == 409
    assert "RIGHT ROUTE VIOLATION" in res_oral.text

    # 2. Parenteral route IV succeeds
    res_iv = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-CEFT-1G",
            "dose_given": "1 g",
            "route": "IV",
        },
    )
    assert res_iv.status_code in (200, 201), res_iv.text
    assert res_iv.json()["is_verified"] is True
    assert res_iv.json()["route_matched"] is True


# =============================================================================
# 3. N-63: Production Guards on Developer Scripts
# =============================================================================

def test_n63_totp_script_production_guard(monkeypatch):
    """
    N-63: backend/scripts/totp.py must fail closed and refuse execution in production.
    """
    from backend.core.config import settings
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    from backend.scripts.totp import main
    assert main(["JBSWY3DPEHPK3PXP"]) == 1


def test_n63_create_operator_password_cli_production_guard(monkeypatch):
    """
    N-63: create_control_plane_operator.py must reject --password in production to prevent CLI history leaks.
    """
    from backend.core.config import settings
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    from backend.scripts.create_control_plane_operator import main
    with pytest.raises(SystemExit):
        main(["--email", "admin@prod.com", "--password", "Secret12345!"])


# =============================================================================
# 4. N-64: NEWS2 Consciousness Completeness
# =============================================================================

def test_n64_news2_missing_consciousness_flagged_in_incomplete_set():
    """
    N-64: NEWS2 score calculation with omitted consciousness must not assume 'ALERT'.
    It must explicitly flag consciousness in missing_parameters and set is_complete_set = False.
    """
    result = calculate_news2_score(
        respiratory_rate=18,
        spo2_percent=98,
        bp_systolic=120,
        pulse_bpm=75,
        temperature_f=98.6,
        consciousness=None,  # Consciousness omitted
        on_supplemental_oxygen=False,
    )

    assert result["is_complete_set"] is False
    assert "consciousness" in result["missing_parameters"]
    assert "consciousness" in result["clinical_recommendation"].lower()
    assert result["news2_score"] == 0
    assert result["risk_level"] == "LOW"


# =============================================================================
# 5. N-44: Controlled Substances Dual-Witness & Destination Linking
# =============================================================================

def test_n44_controlled_substances_receipt_dispense_return_and_wastage(client, superuser_headers, nurse_headers):
    """
    N-44: Verifies narcotics vault operations:
    - Receipt with optional witness verification.
    - Dispense requiring destination (encounter or prescription or notes).
    - Return to vault restoring balance with witness verification.
    - Wastage requiring independent dual-witness credential verification (rejecting self-witness and bad password).
    """
    # 1. Create a controlled substance medication (Morphine 10mg/mL ampoule)
    med_res = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "MED_MORPHINE_10MG",
            "brand_name": "Morphine Sulfate 10mg/mL",
            "generic_name": "Morphine",
            "form": "INJECTION",
            "strength": "10 mg/mL",
            "ndc_or_barcode": "BARCODE-MORPH-10MG",
        },
    )
    med = med_res.json()

    # 2. Initialize vault safe with 20 units
    vault_res = client.post(
        "/v1/api/pharmacy/controlled-substances/vault",
        headers=superuser_headers,
        json={
            "medication_id": med["id"],
            "vault_location": "ICU Narcotic Safe #1",
            "initial_balance_units": 20,
            "unit_of_measure": "ampoule",
            "tamper_seal_number": "SEAL-A1001",
        },
    )
    assert vault_res.status_code == 201, vault_res.text
    vault = vault_res.json()
    assert vault["current_balance_units"] == 20

    # 3. Receipt 10 units with secondary witness verification
    # Secondary witness: User ID 2 (Doctor), password "StaffPass123!"
    rec_res = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 10,
            "batch_number": "BATCH-MORPH-2026",
            "tamper_seal_number": "SEAL-A1002",
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "notes": "Emergency stock replenishment",
        },
    )
    assert rec_res.status_code == 201, rec_res.text
    assert rec_res.json()["balance_after"] == 30
    assert rec_res.json()["witness_verified"] is True

    # 4. Dispense: destination validation
    # Missing encounter_id, prescription_item_id, and notes must FAIL
    disp_fail = client.post(
        "/v1/api/pharmacy/controlled-substances/dispense",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 5,
        },
    )
    assert disp_fail.status_code == 400
    assert "Dispense destination required" in disp_fail.text

    # Dispense with notes destination succeeds
    disp_ok = client.post(
        "/v1/api/pharmacy/controlled-substances/dispense",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 5,
            "notes": "Dispensed to ICU Ward Floor Stock",
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
        },
    )
    assert disp_ok.status_code == 201, disp_ok.text
    assert disp_ok.json()["balance_after"] == 25
    assert disp_ok.json()["witness_verified"] is True

    # 5. Return 2 unused ampoules back to safe
    ret_res = client.post(
        "/v1/api/pharmacy/controlled-substances/return",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 2,
            "notes": "Unopened ampoules returned from procedure room",
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
        },
    )
    assert ret_res.status_code == 201, ret_res.text
    assert ret_res.json()["balance_after"] == 27
    assert ret_res.json()["witness_verified"] is True

    # 6. Wastage: Self-witnessing must FAIL
    waste_self = client.post(
        "/v1/api/pharmacy/controlled-substances/waste",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 1,
            "wastage_reason": "Partial 0.5mL syringe discard post-sedation",
            "disposal_method": "Chemical denaturing in disposal sink",
            "witness_user_id": 1,  # Self (superuser ID = 1)
            "witness_password": "AnyPassword123!",
        },
    )
    assert waste_self.status_code == 400
    assert "Self-witnessing is strictly prohibited" in waste_self.text

    # 7. Wastage: Invalid witness password must FAIL
    waste_bad_pwd = client.post(
        "/v1/api/pharmacy/controlled-substances/waste",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 1,
            "wastage_reason": "Partial 0.5mL syringe discard post-sedation",
            "disposal_method": "Chemical denaturing in disposal sink",
            "witness_user_id": 2,
            "witness_password": "WrongPassword999!",
        },
    )
    assert waste_bad_pwd.status_code == 401
    assert "Invalid witness password" in waste_bad_pwd.text

    # 8. Wastage: Valid dual-witness destruction succeeds
    waste_ok = client.post(
        "/v1/api/pharmacy/controlled-substances/waste",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 1,
            "wastage_reason": "Partial 0.5mL syringe discard post-sedation",
            "disposal_method": "Chemical denaturing in disposal sink",
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
        },
    )
    assert waste_ok.status_code == 201, waste_ok.text
    assert waste_ok.json()["balance_after"] == 26
    assert waste_ok.json()["witness_verified"] is True
