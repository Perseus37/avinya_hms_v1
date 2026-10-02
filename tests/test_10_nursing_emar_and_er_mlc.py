def _create_encounter(client, doctor_headers, encounter_type="IPD"):
    p = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={"full_name": f"{encounter_type} Patient", "date_of_birth": "1985-06-15T00:00:00Z", "gender": "MALE"},
    ).json()
    enc = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={"patient_id": p["id"], "encounter_type": encounter_type, "primary_doctor_id": 2, "department_id": 1},
    ).json()
    return p, enc


def test_01_record_inpatient_emar_dose_administration_and_hold_notes_guard(
    client, doctor_headers, nurse_headers, pharmacist_headers
):
    """Scenario 1: Nurse administers prescribed dose via eMAR; marking a dose HELD without clinical notes returns 400."""
    med = client.post(
        "/v1/api/pharmacy/medications",
        headers=pharmacist_headers,
        json={"code": "INJ_PANTO_40", "brand_name": "Pantocid IV", "generic_name": "Pantoprazole", "form": "INJECTION", "strength": "40mg"},
    ).json()

    _, enc = _create_encounter(client, doctor_headers, encounter_type="IPD")
    rx = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={"encounter_id": enc["id"], "items": [{"medication_id": med["id"], "dosage_instruction": "40mg IV OD", "requested_quantity": 3}]},
    ).json()
    rx_item_id = rx["items"][0]["id"]

    # 1. Nurse administers first dose -> 201
    adm_res = client.post(
        f"/v1/api/nursing/encounters/{enc['id']}/emar",
        headers=nurse_headers,
        json={"prescription_item_id": rx_item_id, "status": "ADMINISTERED", "dose_given": "40mg IV Slow Push"},
    )
    assert adm_res.status_code == 201
    assert adm_res.json()["status"] == "ADMINISTERED"

    # 2. Marking second dose HELD without mandatory clinical notes is blocked (400)
    hold_fail = client.post(
        f"/v1/api/nursing/encounters/{enc['id']}/emar",
        headers=nurse_headers,
        json={"prescription_item_id": rx_item_id, "status": "HELD", "dose_given": "0mg"},
    )
    assert hold_fail.status_code == 400

    # 3. Marking HELD with clinical notes succeeds (201)
    hold_ok = client.post(
        f"/v1/api/nursing/encounters/{enc['id']}/emar",
        headers=nurse_headers,
        json={"prescription_item_id": rx_item_id, "status": "HELD", "dose_given": "0mg", "notes": "Patient NPO for endoscopy"},
    )
    assert hold_ok.status_code == 201


def test_02_emar_blocks_bedside_administration_if_new_severe_allergy_discovered(
    client, doctor_headers, nurse_headers, pharmacist_headers
):
    """Scenario 2: Prescription written before allergy was known; Nurse charts ANAPHYLACTIC Penicillin allergy -> eMAR blocks Amoxicillin dose with 409!"""
    med = client.post(
        "/v1/api/pharmacy/medications",
        headers=pharmacist_headers,
        json={"code": "INJ_AMOX_1G", "brand_name": "Augmentin IV", "generic_name": "Amoxicillin", "form": "INJECTION", "strength": "1.2g"},
    ).json()

    p, enc = _create_encounter(client, doctor_headers, encounter_type="IPD")
    rx = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={"encounter_id": enc["id"], "items": [{"medication_id": med["id"], "dosage_instruction": "1.2g IV TID", "requested_quantity": 3}]},
    ).json()
    rx_item_id = rx["items"][0]["id"]

    # Later on the ward, patient discloses ANAPHYLACTIC Penicillin allergy (allergy_id=1)
    client.post(f"/v1/api/patients/{p['id']}/allergies", headers=doctor_headers, json={"allergy_id": 1, "severity": "ANAPHYLACTIC", "reaction": "Airway edema"})

    # Nurse attempts to administer the previously prescribed Amoxicillin -> Blocked by Bedside eMAR Hard-Stop (409)!
    emar_block = client.post(
        f"/v1/api/nursing/encounters/{enc['id']}/emar",
        headers=nurse_headers,
        json={"prescription_item_id": rx_item_id, "status": "ADMINISTERED", "dose_given": "1.2g IV"},
    )
    assert emar_block.status_code == 409
    assert "hard-stop" in emar_block.json()["message"].lower()


def test_03_inpatient_fluid_intake_output_balance_calculation(client, doctor_headers, nurse_headers):
    """Scenario 3: Record IV/Oral fluid intake (1500 mL) and Urine/Drain output (950 mL) -> Net Balance = +550 mL."""
    _, enc = _create_encounter(client, doctor_headers, encounter_type="IPD")

    client.post(f"/v1/api/nursing/encounters/{enc['id']}/fluid-logs", headers=nurse_headers, json={"direction": "INTAKE", "fluid_type": "IV Normal Saline 0.9%", "volume_ml": 1000})
    client.post(f"/v1/api/nursing/encounters/{enc['id']}/fluid-logs", headers=nurse_headers, json={"direction": "INTAKE", "fluid_type": "Oral Water & Soup", "volume_ml": 500})
    client.post(f"/v1/api/nursing/encounters/{enc['id']}/fluid-logs", headers=nurse_headers, json={"direction": "OUTPUT", "fluid_type": "Urine (Foley Catheter)", "volume_ml": 800})
    client.post(f"/v1/api/nursing/encounters/{enc['id']}/fluid-logs", headers=nurse_headers, json={"direction": "OUTPUT", "fluid_type": "Surgical Drain", "volume_ml": 150})

    summary = client.get(f"/v1/api/nursing/encounters/{enc['id']}/fluid-balance", headers=doctor_headers).json()
    assert summary["total_intake_ml"] == 1500
    assert summary["total_output_ml"] == 950
    assert summary["net_balance_ml"] == 550
    assert len(summary["logs"]) == 4


def test_04_er_triage_blocks_non_er_encounters_and_sorts_queue_by_acuity(client, doctor_headers, nurse_headers):
    """Scenario 4: ER Triage rejects OPD/IPD encounters (400) and sorts active ER queue by RED_RESUSCITATION first."""
    _, opd_enc = _create_encounter(client, doctor_headers, encounter_type="OPD")
    assert client.post(
        f"/v1/api/er/encounters/{opd_enc['id']}/triage",
        headers=nurse_headers,
        json={"acuity_level": "GREEN_LESS_URGENT", "chief_complaint": "Mild sprain"},
    ).status_code == 400

    # Create 2 ER encounters: first GREEN, second RED
    _, er_green = _create_encounter(client, doctor_headers, encounter_type="ER")
    _, er_red = _create_encounter(client, doctor_headers, encounter_type="ER")

    client.post(
        f"/v1/api/er/encounters/{er_green['id']}/triage",
        headers=nurse_headers,
        json={"acuity_level": "GREEN_LESS_URGENT", "chief_complaint": "Minor ankle twist", "gcs_score": 15, "pain_scale": 3},
    )
    client.post(
        f"/v1/api/er/encounters/{er_red['id']}/triage",
        headers=nurse_headers,
        json={"acuity_level": "RED_RESUSCITATION", "chief_complaint": "Severe polytrauma RTA", "gcs_score": 7, "pain_scale": 9, "is_mlc_suspected": True},
    )

    queue = client.get("/v1/api/er/triage-queue", headers=doctor_headers).json()
    assert len(queue) >= 2
    assert queue[0]["acuity_level"] == "RED_RESUSCITATION"
    assert queue[0]["encounter_id"] == er_red["id"]


def test_05_register_medico_legal_case_police_intimation_and_print_certificate(
    client, doctor_headers, nurse_headers
):
    """Scenario 5: Nurse is blocked (403) from registering an MLC; Doctor registers MLC, stamps police intimation (locked against re-intimation), and prints SHO certificate."""
    _, er_enc = _create_encounter(client, doctor_headers, encounter_type="ER")

    mlc_payload = {
        "encounter_id": er_enc["id"],
        "cause_of_injury": "ROAD_TRAFFIC_ACCIDENT",
        "alleged_history": "Two-wheeler skidded near Hitech City Flyover at 19:30 IST",
        "identification_marks": "1. Mole on right cheek; 2. Old scar on left forearm",
        "brought_by_name": "Ramesh Kumar",
        "brought_by_relation": "Brother",
        "brought_by_phone": "9876543210",
        "police_station_name": "Madhapur Police Station, Cyberabad",
    }

    # 1. Nurse cannot register an official Medico-Legal Case (403)
    assert client.post("/v1/api/er/mlc", headers=nurse_headers, json=mlc_payload).status_code == 403

    # 2. Casualty Medical Officer (Doctor) registers MLC -> 201
    mlc_res = client.post("/v1/api/er/mlc", headers=doctor_headers, json=mlc_payload)
    assert mlc_res.status_code == 201
    mlc = mlc_res.json()
    assert mlc["mlc_number"].startswith("MLC-")

    # 3. Record Police Intimation -> 200, and block duplicate intimation with 409 Forensic Lock
    intim_res = client.patch(
        f"/v1/api/er/mlc/{mlc['id']}/police-intimation",
        headers=doctor_headers,
        json={"police_constable_buckle_no": "PC-4421-CYB", "fir_number": "FIR-2026-0891"},
    )
    assert intim_res.status_code == 200
    assert intim_res.json()["police_intimated_at"] is not None

    assert client.patch(
        f"/v1/api/er/mlc/{mlc['id']}/police-intimation",
        headers=doctor_headers,
        json={"police_constable_buckle_no": "PC-9999"},
    ).status_code == 409

    # 4. Print official MLC Police Intimation Certificate
    print_res = client.get(f"/v1/api/er/mlc/{mlc['id']}/print", headers=doctor_headers)
    assert print_res.status_code == 200
    assert "MEDICO-LEGAL CASE (MLC) POLICE INTIMATION CERTIFICATE" in print_res.text
    assert "Madhapur Police Station, Cyberabad" in print_res.text
    assert "PC-4421-CYB" in print_res.text