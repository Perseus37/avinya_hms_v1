"""
Audit Round 18 Remediation Test Suite.
Verifies remediations for findings identified in external auditor report (Section 18):
1. N-66: An unobtainable lab result (OBX-11 = X) must not close a single-test order as COMPLETED.
   The task is set to SAMPLE_REJECTED with rejection reason and recollection alert, and parent order remains ACTIVE.
2. N-44: Controlled substances (narcotics) perpetual vault safety hardening:
   - Receipt requires mandatory independent secondary witness and valid supply/procurement reference (rejects unreferenced/unwitnessed receipts).
   - Dispense destination rule requires encounter/prescription link or detailed notes >= 10 chars (rejects trivial notes like 'x').
   - Return requires mandatory secondary witness and link to encounter/prescription or detailed return justification (rejects unwitnessed/unjustified returns).
3. N-65: Route recognition for INTRATHECAL (and other standard routes) in BCMA bedside scanning.
4. N-68: Staging environment treated as protected/production by security guards (totp.py and operator --password flag).
5. N-67: Preliminary lab results explicitly flagged with is_preliminary = True in diagnostic task responses.
"""

from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from backend.domains.diagnostics.models import OrderStatusEnum, TaskStatusEnum
from backend.domains.identity.models import User


# =============================================================================
# 1. N-66: Single-Test Diagnostic Order Unobtainable Result (OBX-11 = X)
# =============================================================================

def test_n66_unobtainable_result_does_not_close_single_test_order(client, superuser_headers, doctor_headers):
    """
    N-66: When an analyzer reports OBX-11 = X on a single-test order, the order must NOT
    become COMPLETED. The task status must be SAMPLE_REJECTED, result_value None, rejection reason
    documented, and parent order status must remain ACTIVE for specimen re-collection.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "Unobtainable Order Patient", "date_of_birth": "1988-11-23T00:00:00Z", "gender": "MALE"},
    )
    patient = p_res.json()

    # Single lab test catalog item: Serum Potassium
    srv_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": 1, "code": "LAB_POTASSIUM_N66", "name": "Serum Potassium Urgent", "service_type": "LAB", "base_price_paise": 3500},
    )
    service = srv_res.json()

    # Place order with exactly 1 test
    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    order_id = order_res.json()["id"]
    task_id = order_res.json()["tasks"][0]["id"]

    # Analyzer transmits OBX status X (Hemolyzed specimen, cannot obtain result)
    hl7_msg_x = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261028080000||ORU^R01|MSG-N66-01|P|2.3\r"
        f"PID|1||{patient['mrn']}||Unobtainable^Patient||19881123|M\r"
        f"OBR|1|{task_id}||LAB_POTASSIUM_N66^Potassium|||20261028075000|||||||||||||||||F\r"
        "OBX|1|NM|K^Potassium||HEMOLYZED|mmol/L|3.5-5.0||||X\r"
    )

    res = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_msg_x})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "PROCESSED"

    # Verify task state: SAMPLE_REJECTED, result_value None, rejection reason populated
    tasks_res = client.get(f"/v1/api/diagnostics/orders/{order_id}/tasks", headers=doctor_headers)
    assert tasks_res.status_code == 200
    task_data = tasks_res.json()["items"][0]
    assert task_data["status"] == "SAMPLE_REJECTED", f"Expected SAMPLE_REJECTED, got {task_data['status']}"
    assert task_data["result_value"] is None
    assert "UNOBTAINABLE" in task_data["result_notes"]
    assert "Re-collection required" in task_data["result_notes"]

    # CRITICAL AUDIT ASSERTION: Parent order must NOT be COMPLETED
    ord_check = client.get(f"/v1/api/diagnostics/orders/{order_id}", headers=doctor_headers)
    assert ord_check.status_code == 200
    order_data = ord_check.json()
    assert order_data["status"] == "ACTIVE", f"Order must remain ACTIVE for specimen recollection, got: {order_data['status']}"
    assert order_data["status"] != "COMPLETED", "Single-test order with rejected specimen must NEVER be marked COMPLETED!"


# =============================================================================
# 2. N-44: Controlled Substances (Narcotics) Vault Security & Policy
# =============================================================================

def test_n44_controlled_substances_strict_policy_and_destination_controls(client, superuser_headers, nurse_headers):
    """
    N-44: Verifies hardened narcotics vault controls:
    1. Receipt requires mandatory secondary witness and procurement reference (rejects unwitnessed/unreferenced receipt).
    2. Dispense destination rule rejects trivial text like 'x' (requires min 10 chars or encounter link).
    3. Return requires mandatory secondary witness and clinical link/justification (rejects unwitnessed/unjustified returns).
    """
    # 1. Create controlled substance medication (Fentanyl 50mcg/mL)
    med_res = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "MED_FENTANYL_50MCG",
            "brand_name": "Sublimaze 50mcg/mL",
            "generic_name": "Fentanyl",
            "form": "INJECTION",
            "strength": "50 mcg/mL",
            "ndc_or_barcode": "BARCODE-FENT-50MCG",
            "is_controlled_substance": True,
        },
    )
    med = med_res.json()

    # 2. Initialize vault safe with 20 units
    v_res = client.post(
        "/v1/api/pharmacy/controlled-substances/vault",
        headers=superuser_headers,
        json={
            "medication_id": med["id"],
            "vault_location": "Trauma ICU Narcotic Safe #2",
            "initial_balance_units": 20,
            "unit_of_measure": "ampoule",
            "tamper_seal_number": "SEAL-B2001",
        },
    )
    assert v_res.status_code == 201
    vault = v_res.json()

    # --- RECEIPT CONTROLS ---
    # 3a. Receipt without secondary witness must FAIL
    rec_no_witness = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 100,
            "batch_number": "BATCH-FENT-100",
            "notes": "PO-99124 - Supplier Delivery",
        },
    )
    assert rec_no_witness.status_code in (400, 422), rec_no_witness.text

    # 3b. Receipt without procurement reference must FAIL
    rec_no_ref = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 100,
            "batch_number": "BATCH-FENT-100",
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "notes": "   ",  # Blank notes
        },
    )
    assert rec_no_ref.status_code in (400, 422), rec_no_ref.text

    # 3c. Valid receipt with dual-witness and PO reference succeeds
    rec_ok = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 100,
            "batch_number": "BATCH-FENT-100",
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "notes": "PO-99124 - Verified Central Pharmacy Intake",
        },
    )
    assert rec_ok.status_code == 201, rec_ok.text
    assert rec_ok.json()["balance_after"] == 120
    assert rec_ok.json()["witness_verified"] is True

    # --- DISPENSE DESTINATION CONTROLS ---
    # 4a. Dispense with trivial note 'x' must FAIL
    disp_trivial_note = client.post(
        "/v1/api/pharmacy/controlled-substances/dispense",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 1,
            "notes": "x",  # Trivial note
        },
    )
    assert disp_trivial_note.status_code == 400
    assert "Dispense destination required" in disp_trivial_note.text

    # 4b. Dispense with detailed ward destination (>= 10 chars) succeeds
    disp_ok = client.post(
        "/v1/api/pharmacy/controlled-substances/dispense",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 5,
            "notes": "Dispensed to ICU Ward Floor Stock - Crash Cart B",
        },
    )
    assert disp_ok.status_code == 201, disp_ok.text
    assert disp_ok.json()["balance_after"] == 115

    # --- RETURN CONTROLS ---
    # 5a. Return without witness must FAIL
    ret_no_witness = client.post(
        "/v1/api/pharmacy/controlled-substances/return",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 2,
            "notes": "Unused ampoules from procedure room",
        },
    )
    assert ret_no_witness.status_code in (400, 422), ret_no_witness.text

    # 5b. Return without clinical link / justification must FAIL
    ret_no_reason = client.post(
        "/v1/api/pharmacy/controlled-substances/return",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 2,
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "notes": "x",  # Trivial note
        },
    )
    assert ret_no_reason.status_code in (400, 422), ret_no_reason.text

    # 5c. Valid return with dual-witness and clinical justification succeeds
    ret_ok = client.post(
        "/v1/api/pharmacy/controlled-substances/return",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 2,
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "notes": "Unopened ampoules returned from procedure room 4",
        },
    )
    assert ret_ok.status_code == 201, ret_ok.text
    assert ret_ok.json()["balance_after"] == 117
    assert ret_ok.json()["witness_verified"] is True


# =============================================================================
# 3. N-65: Intrathecal Route Recognition in BCMA Bedside Scanning
# =============================================================================

def test_n65_bcma_intrathecal_route_recognized(client, superuser_headers, nurse_headers):
    """
    N-65: INTRATHECAL route was previously not recognized in ROUTE_ALIASES, causing it to
    be treated as omitted and rejected. Verifies INTRATHECAL is recognized as a valid parenteral route.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "Intrathecal Route Patient", "date_of_birth": "1982-04-14T00:00:00Z", "gender": "FEMALE"},
    )
    patient = p_res.json()

    enc_res = client.post(
        "/v1/api/adt/encounters",
        headers=superuser_headers,
        json={"patient_id": patient["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1},
    )
    encounter = enc_res.json()

    # Bupivacaine 0.5% (Heavy) Spinal / Intrathecal injection
    med_res = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "MED_BUPIVACAIN_SPINAL",
            "brand_name": "Sensorcaine Spinal 0.5%",
            "generic_name": "Bupivacaine",
            "form": "INJECTION",
            "strength": "15 mg/3ml",
            "ndc_or_barcode": "BARCODE-BUPI-SPINAL",
        },
    )
    med = med_res.json()

    # Prescribe 15 mg intrathecal for spinal anesthesia
    rx_res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=superuser_headers,
        json={
            "encounter_id": encounter["id"],
            "items": [
                {
                    "medication_id": med["id"],
                    "dosage_instruction": "15 mg intrathecal stat",
                    "requested_quantity": 1,
                }
            ],
        },
    )
    rx_item_id = rx_res.json()["items"][0]["id"]

    # Administer with route="INTRATHECAL"
    res_admin = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-BUPI-SPINAL",
            "dose_given": "15 mg",
            "route": "INTRATHECAL",
        },
    )
    assert res_admin.status_code in (200, 201), res_admin.text
    data = res_admin.json()
    assert data["is_verified"] is True
    assert data["route_matched"] is True


# =============================================================================
# 4. N-68: Staging Environment Protected Under Production Security Guards
# =============================================================================

def test_n68_staging_environment_treated_as_protected(monkeypatch):
    """
    N-68: When ENVIRONMENT='staging', security guards must treat staging like production:
    - totp.py utility refuses execution (returns 1).
    - create_control_plane_operator.py refuses --password CLI argument (raises SystemExit).
    - settings.is_production() evaluates to True.
    """
    from backend.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    assert settings.is_production() is True

    # 1. totp.py script refuses execution in staging
    from backend.scripts.totp import main as totp_main
    assert totp_main(["JBSWY3DPEHPK3PXP"]) == 1

    # 2. create_control_plane_operator.py refuses raw --password in staging
    from backend.scripts.create_control_plane_operator import main as operator_main
    with pytest.raises(SystemExit):
        operator_main(["--email", "admin@staging.hospital.internal", "--password", "SecretPassword123!"])


# =============================================================================
# 5. N-67: Preliminary Diagnostic Task Display / Storage Flag
# =============================================================================

def test_n67_preliminary_diagnostic_task_explicitly_flagged(client, superuser_headers, doctor_headers):
    """
    N-67: When an analyzer transmits OBX-11 = P (preliminary), the diagnostic task must
    explicitly expose is_preliminary = True so client screens and print templates never
    display unverified values as finalized results.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "Preliminary Flag Patient", "date_of_birth": "1994-07-19T00:00:00Z", "gender": "FEMALE"},
    )
    patient = p_res.json()

    srv_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": 1, "code": "LAB_PRELIM_FLAG", "name": "Preliminary Gram Stain", "service_type": "LAB", "base_price_paise": 2500},
    )
    service = srv_res.json()

    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    order_id = order_res.json()["id"]
    task_id = order_res.json()["tasks"][0]["id"]

    # Transmit preliminary result
    hl7_msg_p = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261028083000||ORU^R01|MSG-N67-01|P|2.3\r"
        f"PID|1||{patient['mrn']}||Prelim^Patient||19940719|F\r"
        f"OBR|1|{task_id}||LAB_PRELIM_FLAG^Gram Stain|||20261028082000|||||||||||||||||P\r"
        "OBX|1|ST|GRAM^Stain||Few Gram-negative bacilli seen||||||P\r"
    )
    res_p = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_msg_p})
    assert res_p.status_code == 200

    tasks_res = client.get(f"/v1/api/diagnostics/orders/{order_id}/tasks", headers=doctor_headers)
    assert tasks_res.status_code == 200
    task_item = tasks_res.json()["items"][0]
    assert task_item["status"] == "IN_PROGRESS"
    assert task_item["is_preliminary"] is True, "Task with preliminary result must have is_preliminary == True"
    assert "Few Gram-negative bacilli" in task_item["result_value"]

