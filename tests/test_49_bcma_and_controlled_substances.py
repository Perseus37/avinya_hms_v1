"""
Tests for Enterprise Track 2:
- Bedside Barcode Medication Administration (BCMA) 5-Rights Verification
- Controlled Substances (Narcotics) Perpetual Vault & Dual-Witness Wastage Register
"""

from datetime import datetime, timedelta, timezone
import pytest


def _create_patient(client, headers, name="BCMA Test Patient", dob="1988-06-15T00:00:00Z", gender="FEMALE"):
    res = client.post(
        "/v1/api/patients",
        headers=headers,
        json={
            "full_name": name,
            "date_of_birth": dob,
            "gender": gender,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_encounter(client, headers, patient_id):
    res = client.post(
        "/v1/api/adt/encounters",
        headers=headers,
        json={
            "patient_id": patient_id,
            "encounter_type": "IPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_medication(client, headers, code, brand, generic, is_controlled=False, ndc=None):
    res = client.post(
        "/v1/api/pharmacy/medications",
        headers=headers,
        json={
            "code": code,
            "brand_name": brand,
            "generic_name": generic,
            "form": "INJECTION" if is_controlled else "TABLET",
            "strength": "50mcg/ml" if is_controlled else "500mg",
            "is_controlled_substance": is_controlled,
            "ndc_or_barcode": ndc,
        },
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


def _create_prescription(client, headers, encounter_id, med_id, instructions="1 tablet twice daily"):
    res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=headers,
        json={
            "encounter_id": encounter_id,
            "items": [
                {
                    "medication_id": med_id,
                    "dosage_instruction": instructions,
                    "requested_quantity": 10,
                }
            ],
        },
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


# =============================================================================
# 1. Barcode Medication Administration (BCMA) 5-Rights Tests
# =============================================================================

def test_bcma_five_rights_successful_bedside_administration(client, superuser_headers, nurse_headers):
    """
    Scenario 1: Happy path bedside BCMA 5-rights scan matching patient wristband,
    medication barcode, dose, route, and on-time window. Atomically generates eMAR log.
    """
    patient = _create_patient(client, superuser_headers, name="BCMA Verified Patient")
    encounter = _create_encounter(client, superuser_headers, patient["id"])
    med = _create_medication(
        client,
        superuser_headers,
        code="MED_AMOX_BCMA",
        brand="Amoxicillin 500mg",
        generic="Amoxicillin",
        ndc="BARCODE-AMOX-12345",
    )
    rx = _create_prescription(client, superuser_headers, encounter["id"], med["id"])
    rx_item_id = rx["items"][0]["id"]

    now_iso = datetime.now(timezone.utc).isoformat()

    # Nurse scans wristband (MRN) and medication NDC/barcode
    payload = {
        "encounter_id": encounter["id"],
        "prescription_item_id": rx_item_id,
        "scanned_patient_barcode": patient["mrn"],
        "scanned_medication_barcode": "BARCODE-AMOX-12345",
        "dose_given": "500mg",
        "scheduled_time": now_iso,
        "override_warning": False,
    }

    res = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json=payload,
    )
    assert res.status_code in (200, 201), res.text
    body = res.json()

    assert body["is_verified"] is True
    assert body["verification_status"] == "VERIFIED"
    assert body["patient_matched"] is True
    assert body["medication_matched"] is True
    assert body["time_status"] == "ON_TIME"
    assert body["emar_log_id"] is not None
    assert body["bcma_log_id"] is not None


def test_bcma_patient_wristband_mismatch_triggers_critical_hard_stop(client, superuser_headers, nurse_headers):
    """
    Scenario 2: Nurse accidentally scans another patient's wristband barcode.
    Must immediately trigger HTTP 409 Critical Hard-Stop and log audit event.
    """
    patient_a = _create_patient(client, superuser_headers, name="Patient Alpha")
    patient_b = _create_patient(client, superuser_headers, name="Patient Beta")

    encounter_a = _create_encounter(client, superuser_headers, patient_a["id"])
    med = _create_medication(client, superuser_headers, code="MED_CIPRO_BCMA", brand="Cipro", generic="Ciprofloxacin")
    rx = _create_prescription(client, superuser_headers, encounter_a["id"], med["id"])
    rx_item_id = rx["items"][0]["id"]

    # Nurse scans patient B's wristband while administering for patient A
    payload = {
        "encounter_id": encounter_a["id"],
        "prescription_item_id": rx_item_id,
        "scanned_patient_barcode": patient_b["mrn"],
        "scanned_medication_barcode": med["code"],
        "dose_given": "500mg",
        "override_warning": False,
    }

    res = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json=payload,
    )
    assert res.status_code == 409, res.text
    assert "BCMA CRITICAL HARD-STOP" in res.text
    assert "RIGHT PATIENT VIOLATION" in res.text


def test_bcma_wrong_medication_scan_triggers_critical_hard_stop(client, superuser_headers, nurse_headers):
    """
    Scenario 3: Nurse scans an incorrect medication vial/packet.
    Must trigger HTTP 409 Critical Hard-Stop prohibiting dose administration.
    """
    patient = _create_patient(client, superuser_headers, name="Med Mismatch Patient")
    encounter = _create_encounter(client, superuser_headers, patient["id"])
    med_prescribed = _create_medication(client, superuser_headers, code="MED_METRO_BCMA", brand="Metronidazole", generic="Metronidazole")
    rx = _create_prescription(client, superuser_headers, encounter["id"], med_prescribed["id"])
    rx_item_id = rx["items"][0]["id"]

    payload = {
        "encounter_id": encounter["id"],
        "prescription_item_id": rx_item_id,
        "scanned_patient_barcode": patient["mrn"],
        "scanned_medication_barcode": "MED_SOME_OTHER_DRUG",
        "dose_given": "400mg",
        "override_warning": False,
    }

    res = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json=payload,
    )
    assert res.status_code == 409, res.text
    assert "BCMA CRITICAL HARD-STOP" in res.text
    assert "RIGHT DRUG VIOLATION" in res.text


def test_bcma_timing_window_early_late_warning_and_clinical_override(client, superuser_headers, nurse_headers):
    """
    Scenario 4: Nurse administers scheduled dose 3 hours early.
    Triggers warning block without override, and succeeds with documented clinical override reason.
    """
    patient = _create_patient(client, superuser_headers, name="Timing Window Patient")
    encounter = _create_encounter(client, superuser_headers, patient["id"])
    med = _create_medication(client, superuser_headers, code="MED_CEFT_BCMA", brand="Ceftriaxone", generic="Ceftriaxone")
    rx = _create_prescription(client, superuser_headers, encounter["id"], med["id"])
    rx_item_id = rx["items"][0]["id"]

    # Scheduled 3 hours in future
    future_sched = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()

    # Step 4a: Scan without override -> blocked with warning
    payload_no_override = {
        "encounter_id": encounter["id"],
        "prescription_item_id": rx_item_id,
        "scanned_patient_barcode": patient["mrn"],
        "scanned_medication_barcode": med["code"],
        "scheduled_time": future_sched,
        "override_warning": False,
    }
    res = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json=payload_no_override,
    )
    assert res.status_code == 409, res.text
    assert "BCMA TIME WINDOW WARNING" in res.text
    assert "early" in res.text

    # Step 4b: Scan with documented clinical override
    payload_with_override = {
        **payload_no_override,
        "override_warning": True,
        "override_reason": "Physician urgent verbal order to administer pre-procedure dose immediately",
    }
    res = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json=payload_with_override,
    )
    assert res.status_code in (200, 201), res.text
    body = res.json()
    assert body["is_verified"] is True
    assert body["verification_status"] == "WARNING_OVERRIDDEN"
    assert body["time_status"] == "EARLY"
    assert body["emar_log_id"] is not None


# =============================================================================
# 2. Controlled Substances (Narcotics) Perpetual Vault & Wastage Tests
# =============================================================================

def test_controlled_substance_vault_lifecycle_receipt_and_dispense(client, superuser_headers, pharmacist_headers):
    """
    Scenario 5: Initialize double-locked narcotics vault, receive replenishment stock,
    dispense units to encounter, and verify balance overdraw prevention.
    """
    med = _create_medication(
        client,
        superuser_headers,
        code="MED_FENT_VAULT",
        brand="Fentanyl Citrate 50mcg/ml",
        generic="Fentanyl",
        is_controlled=True,
    )

    # 1. Initialize vault with 10 units
    v_res = client.post(
        "/v1/api/pharmacy/controlled-substances/vault",
        headers=pharmacist_headers,
        json={
            "medication_id": med["id"],
            "vault_location": "ICU_NARCOTICS_SAFE_A",
            "initial_balance_units": 10,
            "unit_of_measure": "AMPOULE",
            "tamper_seal_number": "SEAL-A101",
        },
    )
    assert v_res.status_code == 201, v_res.text
    vault = v_res.json()
    assert vault["current_balance_units"] == 10
    assert vault["is_locked"] is True

    # Duplicate vault creation for same medication fails
    dup_res = client.post(
        "/v1/api/pharmacy/controlled-substances/vault",
        headers=pharmacist_headers,
        json={
            "medication_id": med["id"],
            "initial_balance_units": 5,
        },
    )
    assert dup_res.status_code == 409

    # 2. Receive stock replenishment (+15 units)
    rcv_res = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=pharmacist_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 15,
            "tamper_seal_number": "SEAL-A102",
            "batch_number": "LOT-FENT-2026-X",
            "notes": "Direct supplier intake verified",
        },
    )
    assert rcv_res.status_code == 201, rcv_res.text
    rcv_data = rcv_res.json()
    assert rcv_data["movement_type"] == "RECEIPT"
    assert rcv_data["balance_before"] == 10
    assert rcv_data["balance_after"] == 25

    # 3. Dispense units from safe (-5 units)
    dsp_res = client.post(
        "/v1/api/pharmacy/controlled-substances/dispense",
        headers=pharmacist_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 5,
            "notes": "Dispensed to OT Room 3",
        },
    )
    assert dsp_res.status_code == 201, dsp_res.text
    dsp_data = dsp_res.json()
    assert dsp_data["movement_type"] == "DISPENSE"
    assert dsp_data["balance_before"] == 25
    assert dsp_data["balance_after"] == 20

    # 4. Overdraw prevention (Attempt to dispense 99 units when only 20 available)
    over_res = client.post(
        "/v1/api/pharmacy/controlled-substances/dispense",
        headers=pharmacist_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 99,
        },
    )
    assert over_res.status_code == 400
    assert "Insufficient narcotic vault balance" in over_res.text


def test_controlled_substance_dual_witness_wastage_security_controls(client, superuser_headers, pharmacist_headers):
    """
    Scenario 6: Enforces mandatory dual-witness protocol for narcotics wastage/destruction.
    Guarantees that:
    1. Self-witnessing is strictly prohibited (400 Bad Request)
    2. Invalid witness credentials fail authentication (401 Unauthorized)
    3. Valid dual-witness co-signature verifies secondary credentials and updates perpetual balance
    """
    med = _create_medication(
        client,
        superuser_headers,
        code="MED_MORPH_VAULT",
        brand="Morphine Sulfate 10mg/ml",
        generic="Morphine",
        is_controlled=True,
    )

    # Initialize vault with 10 ampoules
    v_res = client.post(
        "/v1/api/pharmacy/controlled-substances/vault",
        headers=pharmacist_headers,
        json={
            "medication_id": med["id"],
            "vault_location": "CENTRAL_VAULT_SAFE",
            "initial_balance_units": 10,
            "unit_of_measure": "AMPOULE",
        },
    )
    assert v_res.status_code == 201
    vault_id = v_res.json()["id"]

    # Pharmacist user id is 4; Doctor user id is 2; Nurse user id is 3.

    # 1. Reject self-witnessing (Pharmacist witness ID == Pharmacist primary ID)
    self_res = client.post(
        "/v1/api/pharmacy/controlled-substances/waste",
        headers=pharmacist_headers,
        json={
            "vault_id": vault_id,
            "quantity_units": 2,
            "witness_user_id": 4,  # Pharmacist herself
            "witness_password": "StaffPass123!",
            "wastage_reason": "Vial broken during transport",
            "disposal_method": "CHEMICAL_NEUTRALIZATION",
        },
    )
    assert self_res.status_code == 400
    assert "Self-witnessing is strictly prohibited" in self_res.text

    # 2. Reject incorrect witness password
    wrong_pass_res = client.post(
        "/v1/api/pharmacy/controlled-substances/waste",
        headers=pharmacist_headers,
        json={
            "vault_id": vault_id,
            "quantity_units": 2,
            "witness_user_id": 2,  # Dr. Gregory House
            "witness_password": "WrongPassword999!",
            "wastage_reason": "Vial broken during transport",
            "disposal_method": "CHEMICAL_NEUTRALIZATION",
        },
    )
    assert wrong_pass_res.status_code == 401
    assert "Invalid witness password" in wrong_pass_res.text

    # 3. Successful dual-witness wastage co-signed by Dr. Gregory House (id=2, pass="StaffPass123!")
    valid_waste_res = client.post(
        "/v1/api/pharmacy/controlled-substances/waste",
        headers=pharmacist_headers,
        json={
            "vault_id": vault_id,
            "quantity_units": 2,
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "wastage_reason": "Partial 6mg administered, residual 4mg destroyed in chemical sink",
            "disposal_method": "CHEMICAL_SINK_DRAIN",
        },
    )
    assert valid_waste_res.status_code == 201, valid_waste_res.text
    waste_data = valid_waste_res.json()
    assert waste_data["movement_type"] == "WASTAGE_DESTROYED"
    assert waste_data["witness_verified"] is True
    assert waste_data["balance_before"] == 10
    assert waste_data["balance_after"] == 8
    assert waste_data["witness_user_id"] == 2

    # 4. Verify movements audit ledger
    ledger_res = client.get(
        f"/v1/api/pharmacy/controlled-substances/movements?vault_id={vault_id}",
        headers=pharmacist_headers,
    )
    assert ledger_res.status_code == 200, ledger_res.text
    movements = ledger_res.json()
    assert len(movements) == 1
    assert movements[0]["movement_type"] == "WASTAGE_DESTROYED"
    assert movements[0]["witness_verified"] is True
