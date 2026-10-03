from datetime import datetime, timezone


def test_01_maternity_birth_registration_auto_creates_newborn_patient_mrn_and_prints_certificate(
    client, doctor_headers
):
    """
    Scenario 1:
    - Register a live birth under a MATERNITY mother's encounter.
    - Verify a new Newborn Patient record (MRN-NB-...) is automatically created in the MPI inheriting mother's contact details.
    - Verify the official Hospital Birth Certificate prints with APGAR scores and Newborn MRN.
    """
    mother = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Ananya Sharma",
            "date_of_birth": "1995-07-12T00:00:00Z",
            "gender": "FEMALE",
            "phone_number": "9848011223",
            "address": "Banjara Hills, Hyderabad",
        },
    ).json()
    enc = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={"patient_id": mother["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1},
    ).json()

    birth_res = client.post(
        "/v1/api/registries/births",
        headers=doctor_headers,
        json={
            "mother_encounter_id": enc["id"],
            "baby_gender": "FEMALE",
            "baby_blood_group": "O+",
            "time_of_birth": datetime.now(timezone.utc).isoformat(),
            "delivery_mode": "LSCS_CESAREAN",
            "birth_weight_kg": 3.15,
            "apgar_1min": 8,
            "apgar_5min": 9,
            "is_stillborn": False,
            "clinical_notes": "Healthy term neonate cried immediately after birth",
        },
    )
    assert birth_res.status_code == 201
    b_data = birth_res.json()
    assert b_data["newborn_mrn"].startswith("MRN-NB-")
    assert b_data["newborn_patient_id"] is not None

    # Verify newborn exists in MPI with mother's phone number
    nb_patient = client.get(f"/v1/api/patients/{b_data['newborn_patient_id']}", headers=doctor_headers).json()
    assert nb_patient["full_name"] == "Baby of Ananya Sharma"
    assert nb_patient["phone_number"] == "9848011223"

    # Print Birth Certificate
    print_res = client.get(f"/v1/api/registries/births/{b_data['id']}/print", headers=doctor_headers)
    assert print_res.status_code == 200
    assert "OFFICIAL HOSPITAL BIRTH RECORD" in print_res.text
    assert b_data["newborn_mrn"] in print_res.text


def test_02_statutory_mccd_death_certification_releases_bed_closes_encounter_and_prints_form4(
    client, superuser_headers, doctor_headers
):
    """
    Scenario 2:
    - Admit an IPD patient to a bed (OCCUPIED).
    - Doctor certifies death via POST /api/registries/deaths -> Automatically releases bed to HOUSEKEEPING,
      marks admission DECEASED, closes encounter as DISCHARGED, and prints Form 4 MCCD Certificate!
    """
    f_id = client.post("/v1/api/ip/floors", headers=superuser_headers, json={"floor_number": 15, "name": "Floor 15"}).json()["floor_id"]
    w_id = client.post("/v1/api/ip/wards", headers=superuser_headers, json={"floor_id": f_id, "department_id": 1, "code": "W-MCCD", "name": "ICU MCCD", "ward_type": "ICU"}).json()["id"]
    r_id = client.post("/v1/api/ip/rooms", headers=superuser_headers, json={"ward_id": w_id, "room_number": "1501", "room_type": "SINGLE", "bed_capacity": 1}).json()["room_id"]
    bed_id = client.post("/v1/api/ip/beds", headers=superuser_headers, json={"room_id": r_id, "bed_number": "1501-A", "bed_type": "ICU", "base_daily_rate_paise": 500000}).json()["bed_id"]

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "MCCD Patient", "date_of_birth": "1945-02-10T00:00:00Z", "gender": "MALE"}).json()
    enc = client.post("/v1/api/adt/encounters", headers=doctor_headers, json={"patient_id": p["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1}).json()
    client.post("/v1/api/adt/admissions", headers=doctor_headers, json={"encounter_id": enc["id"], "admission_type": "EMERGENCY", "admitting_doctor_id": 2, "assigned_bed_id": bed_id})

    d_res = client.post(
        "/v1/api/registries/deaths",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "immediate_cause_diagnosis_id": 1,
            "time_of_death": datetime.now(timezone.utc).isoformat(),
            "manner_of_death": "NATURAL",
            "antecedent_cause_notes": "Severe refractory septic shock",
            "is_autopsy_advised": False,
            "body_handed_over_to": "Suresh Kumar (Son)",
        },
    )
    assert d_res.status_code == 201
    d_data = d_res.json()
    assert d_data["certificate_number"].startswith("MCCD-")

    # Verify encounter is now DISCHARGED
    enc_after = client.get(f"/v1/api/adt/encounters/{enc['id']}", headers=doctor_headers).json()
    assert enc_after["status"] == "DISCHARGED"

    # Print Form 4 MCCD Certificate
    print_res = client.get(f"/v1/api/registries/deaths/{d_data['id']}/print", headers=doctor_headers)
    assert print_res.status_code == 200
    assert "FORM 4: MEDICAL CERTIFICATE OF CAUSE OF DEATH" in print_res.text
    assert "Severe refractory septic shock" in print_res.text


def test_03_ipd_discharge_financial_clearance_gate_blocks_unpaid_finalized_bill_until_settled(
    client, superuser_headers, doctor_headers, billing_headers
):
    """
    Scenario 3:
    - IPD patient has a finalized UNPAID invoice of ₹10,000 (1,000,000 paise).
    - Routine discharge is blocked with 409 Financial Clearance Gate!
    - Paying the invoice (or passing force_financial_override=True) unlocks clinical discharge!
    """
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_GATE", "name": "Gate Cat"}).json()
    svc = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "SVC_GATE_10K", "name": "IPD Package", "service_type": "PROCEDURE", "base_price_paise": 1000000, "is_order_set": False}).json()

    f_id = client.post("/v1/api/ip/floors", headers=superuser_headers, json={"floor_number": 16, "name": "Floor 16"}).json()["floor_id"]
    w_id = client.post("/v1/api/ip/wards", headers=superuser_headers, json={"floor_id": f_id, "department_id": 1, "code": "W-GATE", "name": "Gate Ward", "ward_type": "GENERAL"}).json()["id"]
    r_id = client.post("/v1/api/ip/rooms", headers=superuser_headers, json={"ward_id": w_id, "room_number": "1601", "room_type": "SINGLE", "bed_capacity": 1}).json()["room_id"]
    bed_id = client.post("/v1/api/ip/beds", headers=superuser_headers, json={"room_id": r_id, "bed_number": "1601-A", "bed_type": "STANDARD", "base_daily_rate_paise": 100000}).json()["bed_id"]

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Gate Patient", "date_of_birth": "1985-05-05T00:00:00Z", "gender": "MALE"}).json()
    enc = client.post("/v1/api/adt/encounters", headers=doctor_headers, json={"patient_id": p["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1}).json()
    adm = client.post("/v1/api/adt/admissions", headers=doctor_headers, json={"encounter_id": enc["id"], "admission_type": "ROUTINE", "admitting_doctor_id": 2, "assigned_bed_id": bed_id}).json()

    # Create & Finalize ₹10,000 UNPAID invoice on this encounter
    inv = client.post("/v1/api/billing/invoices", headers=billing_headers, json={"patient_id": p["id"], "encounter_id": enc["id"]}).json()
    client.post(f"/v1/api/billing/invoices/{inv['id']}/items", headers=billing_headers, json={"service_id": svc["id"], "description": "IPD Package", "quantity": 1, "unit_price_paise": 1000000})
    client.patch(f"/v1/api/billing/invoices/{inv['id']}/finalize", headers=billing_headers)

    # 1. Routine discharge blocked by Financial Clearance Gate (409)!
    block_res = client.post(
        f"/v1/api/adt/admissions/{adm['id']}/discharge",
        headers=superuser_headers,
        json={"discharge_disposition": "ROUTINE_DISCHARGE", "discharge_summary": "Attempted discharge with unpaid bill"},
    )
    assert block_res.status_code == 409
    assert "financial clearance gate" in block_res.json()["message"].lower()

    # 2. Settle the ₹10,000 invoice -> Routine discharge now succeeds (200)!
    client.post(f"/v1/api/billing/invoices/{inv['id']}/payments", headers=billing_headers, json={"amount_paid_paise": 1000000, "payment_method": "UPI"})
    ok_res = client.post(
        f"/v1/api/adt/admissions/{adm['id']}/discharge",
        headers=superuser_headers,
        json={"discharge_disposition": "ROUTINE_DISCHARGE", "discharge_summary": "Cleared dues and discharged"},
    )
    assert ok_res.status_code == 200