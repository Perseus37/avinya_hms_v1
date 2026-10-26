"""Tests for CPOE Clinical Drug-Drug Interaction (DDI) & Duplicate Therapy Checks (M-11)."""


def _create_medication(client, headers, code, brand, generic):
    res = client.post(
        "/v1/api/pharmacy/medications",
        headers=headers,
        json={
            "code": code,
            "brand_name": brand,
            "generic_name": generic,
            "form": "TABLET",
            "strength": "10mg",
        },
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


def _create_patient_encounter(client, headers):
    p = client.post(
        "/v1/api/patients",
        headers=headers,
        json={
            "full_name": "Clinical Safety Patient",
            "date_of_birth": "1980-05-15T00:00:00Z",
            "gender": "MALE",
        },
    ).json()
    enc = client.post(
        "/v1/api/adt/encounters",
        headers=headers,
        json={
            "patient_id": p["id"],
            "encounter_type": "OPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    ).json()
    return p, enc


def test_ddi_critical_hard_stop_contraindication(client, doctor_headers, pharmacist_headers):
    """
    Scenario 1: Nitrates + PDE5 inhibitors is an absolute contraindication.
    Prescribing Nitroglycerin + Sildenafil results in a 409 CLINICAL HARD-STOP that cannot be overridden.
    """
    p, enc = _create_patient_encounter(client, doctor_headers)
    nitro = _create_medication(client, pharmacist_headers, "MED_NITRO_01", "Nitrostat", "Nitroglycerin")
    viagra = _create_medication(client, pharmacist_headers, "MED_VIAGRA_01", "Viagra", "Sildenafil")

    # Attempt to prescribe together without override
    res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "clinical_notes": "Angina treatment",
            "items": [
                {"medication_id": nitro["id"], "dosage_instruction": "0.4mg SL", "requested_quantity": 10},
                {"medication_id": viagra["id"], "dosage_instruction": "50mg PRN", "requested_quantity": 4},
            ],
        },
    )
    assert res.status_code == 409
    assert "CLINICAL HARD-STOP" in res.json()["message"]

    # Attempt to bypass with override -> Still blocked (absolute contraindication)
    res_override = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "clinical_notes": "Attempted override",
            "items": [
                {"medication_id": nitro["id"], "dosage_instruction": "0.4mg SL", "requested_quantity": 10},
                {"medication_id": viagra["id"], "dosage_instruction": "50mg PRN", "requested_quantity": 4},
            ],
            "override_ddi_warning": True,
            "ddi_override_justification": "Doctor acknowledges and assumes full responsibility",
        },
    )
    assert res_override.status_code == 409
    assert "CLINICAL HARD-STOP" in res_override.json()["message"]


def test_ddi_critical_warning_and_override_workflow(client, doctor_headers, pharmacist_headers):
    """
    Scenario 2: Anticoagulant (Warfarin) + NSAID (Aspirin) triggers a critical warning.
    Blocked without override, but allowed with documented clinical justification.
    """
    p, enc = _create_patient_encounter(client, doctor_headers)
    warfarin = _create_medication(client, pharmacist_headers, "MED_WARF_01", "Coumadin", "Warfarin")
    aspirin = _create_medication(client, pharmacist_headers, "MED_ASP_01", "Ecosprin", "Aspirin")

    # 1. Unacknowledged -> 409 Conflict
    res_blocked = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "items": [
                {"medication_id": warfarin["id"], "dosage_instruction": "5mg OD", "requested_quantity": 30},
                {"medication_id": aspirin["id"], "dosage_instruction": "75mg OD", "requested_quantity": 30},
            ],
        },
    )
    assert res_blocked.status_code == 409
    assert "CLINICAL INTERACTION WARNING" in res_blocked.json()["message"]

    # 2. Overridden with clinical justification -> 201 Created
    justification = "Cardiology consult: Co-prescription indicated for mechanical valve post-CABG. INR monitored weekly."
    res_ok = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "clinical_notes": "Post-discharge therapy",
            "items": [
                {"medication_id": warfarin["id"], "dosage_instruction": "5mg OD", "requested_quantity": 30},
                {"medication_id": aspirin["id"], "dosage_instruction": "75mg OD", "requested_quantity": 30},
            ],
            "override_ddi_warning": True,
            "ddi_override_justification": justification,
        },
    )
    assert res_ok.status_code == 201
    rx_data = res_ok.json()
    assert justification in rx_data["clinical_notes"]
    assert "CLINICAL DDI OVERRIDE" in rx_data["clinical_notes"]


def test_ddi_duplicate_therapy_detection(client, doctor_headers, pharmacist_headers):
    """
    Scenario 3: Prescribing two NSAIDs simultaneously (Ibuprofen + Diclofenac)
    is flagged as duplicate therapy.
    """
    p, enc = _create_patient_encounter(client, doctor_headers)
    ibup = _create_medication(client, pharmacist_headers, "MED_IBUP_01", "Brufen", "Ibuprofen")
    diclo = _create_medication(client, pharmacist_headers, "MED_DICLO_01", "Voveran", "Diclofenac")

    res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "items": [
                {"medication_id": ibup["id"], "dosage_instruction": "400mg TDS", "requested_quantity": 15},
                {"medication_id": diclo["id"], "dosage_instruction": "50mg BD", "requested_quantity": 10},
            ],
        },
    )
    assert res.status_code == 409
    assert "CLINICAL INTERACTION WARNING" in res.json()["message"]
    assert "Duplicate Therapy" in res.json()["message"]


def test_ddi_standalone_precheck_endpoint(client, doctor_headers, pharmacist_headers):
    """
    Scenario 4: Standalone CPOE precheck endpoint evaluates candidate drug IDs.
    """
    p, _ = _create_patient_encounter(client, doctor_headers)
    nitro = _create_medication(client, pharmacist_headers, "MED_NITRO_02", "Nitrocontin", "Nitroglycerin")
    viagra = _create_medication(client, pharmacist_headers, "MED_VIAGRA_02", "Revatio", "Sildenafil")

    res = client.post(
        "/v1/api/pharmacy/clinical-checks/ddi",
        headers=doctor_headers,
        json={
            "patient_id": p["id"],
            "medication_ids": [nitro["id"], viagra["id"]],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["has_critical_hard_stop"] is True
    assert len(data["findings"]) >= 1
    assert data["findings"][0]["severity"] == "CRITICAL_HARD_STOP"



def test_blank_generic_name_does_not_trigger_false_hard_stop(client, doctor_headers, pharmacist_headers):
    """
    Verifies that a medication with a blank/empty generic name does NOT falsely match
    all drug classes or trigger spurious critical hard stops.
    """
    p, enc = _create_patient_encounter(client, doctor_headers)
    blank_med = _create_medication(client, pharmacist_headers, "MED_BLANK_GEN_01", "HerbalSupp", "   ")
    paracetamol = _create_medication(client, pharmacist_headers, "MED_PCM_01", "Dolo 650", "Paracetamol")

    res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "items": [
                {"medication_id": blank_med["id"], "dosage_instruction": "1 OD", "requested_quantity": 10},
                {"medication_id": paracetamol["id"], "dosage_instruction": "650mg SOS", "requested_quantity": 10},
            ],
        },
    )
    assert res.status_code == 201


def test_ddi_cross_encounter_active_prescription_check(client, doctor_headers, pharmacist_headers):
    """
    Verifies that active prescriptions from an EARLIER encounter for the same patient
    are checked when issuing a new prescription in a subsequent encounter.
    """
    # 1. First Encounter: prescribe Nitroglycerin
    p, enc1 = _create_patient_encounter(client, doctor_headers)
    nitro = _create_medication(client, pharmacist_headers, "MED_NITRO_CROSS_01", "Nitrocontin", "Nitroglycerin")
    sildenafil = _create_medication(client, pharmacist_headers, "MED_SILD_CROSS_01", "Revatio", "Sildenafil")

    res_rx1 = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={
            "encounter_id": enc1["id"],
            "items": [
                {"medication_id": nitro["id"], "dosage_instruction": "2.6mg BD", "requested_quantity": 30},
            ],
        },
    )
    assert res_rx1.status_code == 201

    # 2. Second Encounter for the SAME patient: prescribe Sildenafil -> Hard stop contraindication
    enc2_res = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={
            "patient_id": p["id"],
            "encounter_type": "ER",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert enc2_res.status_code == 201
    enc2 = enc2_res.json()

    res_rx2 = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={
            "encounter_id": enc2["id"],
            "items": [
                {"medication_id": sildenafil["id"], "dosage_instruction": "50mg PRN", "requested_quantity": 4},
            ],
        },
    )
    assert res_rx2.status_code == 409
    assert "CLINICAL HARD-STOP" in res_rx2.json()["message"]


def test_substring_drug_name_does_not_trigger_false_interaction(client, doctor_headers, pharmacist_headers):
    """
    Guarantees word-boundary precision: substring prefix 'hepa' in 'Hepatoglobine'
    does NOT falsely match 'heparin' to trigger an erroneous anticoagulant interaction with Aspirin.
    """
    p, enc = _create_patient_encounter(client, doctor_headers)
    hepa_tonic = _create_medication(client, pharmacist_headers, "MED_HEPATOGLOBINE", "Hepatoglobine Syrup", "Liver tonic")
    aspirin = _create_medication(client, pharmacist_headers, "MED_ASPIRIN_SAFE", "Ecosprin 75", "Aspirin")

    res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "items": [
                {"medication_id": hepa_tonic["id"], "dosage_instruction": "10ml daily", "requested_quantity": 1},
                {"medication_id": aspirin["id"], "dosage_instruction": "75mg daily", "requested_quantity": 10},
            ],
        },
    )
    # Should succeed without clinical hard-stop or anticoagulant warning
    assert res.status_code == 201, res.text

