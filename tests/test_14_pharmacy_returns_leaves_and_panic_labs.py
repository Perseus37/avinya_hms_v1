from datetime import datetime, timedelta, timezone


def test_01_pharmacy_stock_return_restores_batch_inventory_and_credits_draft_invoice(
    client, doctor_headers, pharmacist_headers, billing_headers
):
    """
    Scenario 1:
    - Dispense 10 tablets of Dolo-650 @ ₹5.00 (500 paise) -> Batch stock drops from 100 to 90; DRAFT invoice = 5,000 paise.
    - Return 4 unused tablets via POST /prescriptions/{id}/return -> Batch stock restored to 94;
      Prescription status transitions from DISPENSED back to PARTIAL; DRAFT invoice auto-credited down to 3,000 paise!
    """
    med = client.post(
        "/v1/api/pharmacy/medications",
        headers=pharmacist_headers,
        json={"code": "TAB_RET_650", "brand_name": "Dolo-Return-650", "generic_name": "Paracetamol", "form": "TABLET", "strength": "650mg"},
    ).json()
    batch = client.post(
        "/v1/api/pharmacy/inventory/batches",
        headers=pharmacist_headers,
        json={
            "medication_id": med["id"],
            "batch_number": "BATCH-RET-01",
            "expiry_date": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
            "stock_quantity": 100,
            "unit_price_paise": 500,
        },
    ).json()

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Return Med Patient", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"}).json()
    enc = client.post("/v1/api/adt/encounters", headers=doctor_headers, json={"patient_id": p["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1}).json()

    rx = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={"encounter_id": enc["id"], "items": [{"medication_id": med["id"], "dosage_instruction": "1-1-1", "requested_quantity": 10}]},
    ).json()
    rx_item_id = rx["items"][0]["id"]

    # Dispense all 10 units
    client.post(
        f"/v1/api/pharmacy/prescriptions/{rx['id']}/dispense",
        headers=pharmacist_headers,
        json={"items": [{"prescription_item_id": rx_item_id, "batch_id": batch["id"], "quantity_to_dispense": 10}]},
    )

    # Return 4 unused units to pharmacy
    ret_res = client.post(
        f"/v1/api/pharmacy/prescriptions/{rx['id']}/return",
        headers=pharmacist_headers,
        json={
            "items": [
                {
                    "prescription_item_id": rx_item_id,
                    "batch_id": batch["id"],
                    "quantity_to_return": 4,
                    "return_reason": "Patient discharged early; 4 blister-sealed tablets returned from ward",
                }
            ]
        },
    )
    assert ret_res.status_code == 201
    assert ret_res.json()[0]["credit_amount_paise"] == 2000

    # Verify batch stock restored to 94 (100 - 10 + 4)
    batches = client.get(f"/v1/api/pharmacy/inventory/batches?medication_id={med['id']}", headers=pharmacist_headers).json()["items"]
    assert batches[0]["stock_quantity"] == 94

    # Verify DRAFT invoice total reduced from 5,000 paise to 3,000 paise
    inv = client.get(f"/v1/api/billing/invoices?patient_id={p['id']}", headers=billing_headers).json()["items"][0]
    assert inv["total_amount_paise"] == 3000
    assert inv["line_items"][0]["quantity"] == 6


def test_02_opd_doctor_leave_blackout_guard_and_sequential_daily_token_numbers(
    client, doctor_headers
):
    """
    Scenario 2:
    - Register a DoctorLeave window (14:00 to 18:00) -> Booking at 15:00 returns 409 Conflict.
    - Booking two morning appointments on the same day auto-assigns sequential queue tokens (Token #1, Token #2).
    """
    p1 = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Token 1 Patient", "date_of_birth": "1992-01-01T00:00:00Z", "gender": "FEMALE"}).json()
    p2 = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Token 2 Patient", "date_of_birth": "1994-01-01T00:00:00Z", "gender": "MALE"}).json()

    # Register Doctor Leave for 2026-12-20 14:00 to 18:00 UTC
    lv_res = client.post(
        "/v1/api/opd/doctors/leaves",
        headers=doctor_headers,
        json={
            "doctor_id": 2,
            "start_time": "2026-12-20T14:00:00Z",
            "end_time": "2026-12-20T18:00:00Z",
            "reason": "Attending National Cardiology Symposium",
        },
    )
    assert lv_res.status_code == 201

    # 1. Booking during doctor's leave window -> 409 Conflict
    leave_conflict = client.post(
        "/v1/api/opd/appointments",
        headers=doctor_headers,
        json={"patient_id": p1["id"], "doctor_id": 2, "department_id": 1, "appointment_time": "2026-12-20T15:00:00Z"},
    )
    assert leave_conflict.status_code == 409
    assert "leave" in leave_conflict.json()["message"].lower()

    # 2. Book two morning appointments before leave -> Token #1 and Token #2 assigned automatically
    a1 = client.post(
        "/v1/api/opd/appointments",
        headers=doctor_headers,
        json={"patient_id": p1["id"], "doctor_id": 2, "department_id": 1, "appointment_time": "2026-12-20T09:00:00Z"},
    ).json()
    a2 = client.post(
        "/v1/api/opd/appointments",
        headers=doctor_headers,
        json={"patient_id": p2["id"], "doctor_id": 2, "department_id": 1, "appointment_time": "2026-12-20T09:30:00Z"},
    ).json()
    assert a1["token_number"] == 1
    assert a2["token_number"] == 2


def test_03_chart_patient_comorbidities_and_encounter_systemic_physical_exams(
    client, superuser_headers, doctor_headers
):
    """Scenario 3: Link ComorbidityMaster to Patient chart and record systemic PhysicalExaminationMaster findings on an Encounter."""
    cm = client.post(
        "/v1/api/clinical/comorbidities",
        headers=superuser_headers,
        json={"department_id": 1, "code": "CM_T2DM_15", "name": "Type 2 Diabetes Mellitus", "icd10_code": "E11.9"},
    ).json()
    pe = client.post(
        "/v1/api/clinical/physical-exams",
        headers=superuser_headers,
        json={"department_id": 1, "body_system": "RESPIRATORY", "code": "PE_CHEST_AUSC", "name": "Bilateral Chest Auscultation"},
    ).json()

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Comorbid Patient", "date_of_birth": "1968-04-10T00:00:00Z", "gender": "MALE"}).json()
    enc = client.post("/v1/api/adt/encounters", headers=doctor_headers, json={"patient_id": p["id"], "encounter_type": "OPD", "primary_doctor_id": 2, "department_id": 1}).json()

    # 1. Chart chronic comorbidity on patient
    cm_res = client.post(
        f"/v1/api/clinical/patients/{p['id']}/comorbidities",
        headers=doctor_headers,
        json={"comorbidity_id": cm["id"], "clinical_remarks": "On Metformin 500mg BD for 5 years"},
    )
    assert cm_res.status_code == 201
    assert cm_res.json()["comorbidity"]["code"] == "CM_T2DM_15"

    # 2. Marking physical exam abnormal (is_normal=False) without findings_notes returns 400
    assert client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/physical-exams",
        headers=doctor_headers,
        json={"exam_master_id": pe["id"], "is_normal": False},
    ).status_code == 400

    # 3. Record abnormal respiratory findings with notes -> 201
    pe_res = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/physical-exams",
        headers=doctor_headers,
        json={"exam_master_id": pe["id"], "is_normal": False, "findings_notes": "Coarse basal crepitations heard in right lower lobe"},
    )
    assert pe_res.status_code == 201
    assert pe_res.json()["is_normal"] is False


def test_04_lis_hemolyzed_sample_rejection_recollection_and_critical_panic_value_alert(
    client, superuser_headers, doctor_headers
):
    """
    Scenario 4:
    - Lab rejects a hemolyzed potassium sample (SAMPLE_REJECTED requires rejection_reason).
    - Phlebotomist recollects sample (SAMPLE_REJECTED -> SAMPLE_COLLECTED -> RESULT_ENTERED) with is_critical_panic=True (K+ = 6.9 mEq/L).
    - GET /api/diagnostics/tasks/panic-alerts surfaces the critical panic task immediately!
    """
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_ELECTRO", "name": "Electrolytes"}).json()
    k_svc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": cat["id"], "code": "LAB_SERUM_K", "name": "Serum Potassium (K+)", "service_type": "LAB", "base_price_paise": 30000, "is_order_set": False},
    ).json()

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Panic Lab Patient", "date_of_birth": "1975-09-12T00:00:00Z", "gender": "FEMALE"}).json()
    order = client.post("/v1/api/diagnostics/orders", headers=doctor_headers, json={"patient_id": p["id"], "ordered_service_ids": [k_svc["id"]]}).json()
    task_id = order["tasks"][0]["id"]

    # 1. Rejecting sample without rejection_reason is blocked (400)
    assert client.patch(
        f"/v1/api/diagnostics/tasks/{task_id}/result",
        headers=doctor_headers,
        json={"status": "SAMPLE_REJECTED"},
    ).status_code == 400

    # 2. Reject hemolyzed sample with reason -> 200
    rej_res = client.patch(
        f"/v1/api/diagnostics/tasks/{task_id}/result",
        headers=doctor_headers,
        json={"status": "SAMPLE_REJECTED", "rejection_reason": "Grossly hemolyzed specimen; recollect in heparin tube"},
    )
    assert rej_res.status_code == 200

    # 3. Recollect sample and enter Critical Panic Value (K+ 6.9 mEq/L)
    client.patch(f"/v1/api/diagnostics/tasks/{task_id}/result", headers=doctor_headers, json={"status": "SAMPLE_COLLECTED"})
    client.patch(
        f"/v1/api/diagnostics/tasks/{task_id}/result",
        headers=doctor_headers,
        json={"status": "RESULT_ENTERED", "result_value": "6.9 mEq/L", "result_notes": "CRITICAL HYPERKALEMIA", "is_critical_panic": True},
    )

    panic_list = client.get("/v1/api/diagnostics/tasks/panic-alerts", headers=doctor_headers).json()
    assert any(t["id"] == task_id and t["result_value"] == "6.9 mEq/L" for t in panic_list)