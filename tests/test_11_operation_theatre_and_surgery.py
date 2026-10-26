from datetime import datetime, timedelta, timezone


def _setup_ipd_surgical_case(client, superuser_headers, doctor_headers, ot_code="OT_MOD_1"):
    cat = client.post(
        "/v1/api/diagnostics/categories",
        headers=superuser_headers,
        json={"department_id": 1, "code": f"CAT_{ot_code}", "name": "Surgical Procedures"},
    ).json()
    proc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={
            "category_id": cat["id"],
            "code": f"PROC_{ot_code}",
            "name": "Total Hip Arthroplasty",
            "service_type": "PROCEDURE",
            "base_price_paise": 8000000,  # ₹80,000
            "is_order_set": False,
        },
    ).json()
    ot = client.post(
        "/v1/api/ot/theatres",
        headers=superuser_headers,
        json={
            "code": ot_code,
            "name": f"Modular Theatre {ot_code}",
            "theatre_type": "ORTHO_TRAUMA",
            "hourly_rate_paise": 500000,  # ₹5,000/hour
        },
    ).json()

    p = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={"full_name": "Surgical Patient", "date_of_birth": "1972-11-20T00:00:00Z", "gender": "MALE"},
    ).json()
    enc = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={"patient_id": p["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1},
    ).json()
    return p, enc, ot, proc


def test_01_schedule_surgery_and_block_overlapping_theatre_or_surgeon_bookings(
    client, superuser_headers, doctor_headers
):
    """Scenario 1: Schedule a surgery in OT_1; overlapping bookings in the same theatre or for the same lead surgeon return 409."""
    _, enc1, ot1, proc = _setup_ipd_surgical_case(client, superuser_headers, doctor_headers, ot_code="OT_CONF_1")
    ot2 = client.post(
        "/v1/api/ot/theatres",
        headers=superuser_headers,
        json={"code": "OT_CONF_2", "name": "Modular Theatre 2", "theatre_type": "MODULAR_MAJOR", "hourly_rate_paise": 400000},
    ).json()

    start_t = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
    end_t = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)

    s1 = client.post(
        "/v1/api/ot/surgeries",
        headers=doctor_headers,
        json={
            "encounter_id": enc1["id"],
            "theatre_id": ot1["id"],
            "procedure_service_id": proc["id"],
            "lead_surgeon_id": 2,
            "scheduled_start_time": start_t.isoformat(),
            "scheduled_end_time": end_t.isoformat(),
        },
    )
    assert s1.status_code == 201

    p2 = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Second Surg Patient", "date_of_birth": "1980-01-01T00:00:00Z", "gender": "FEMALE"}).json()
    enc2 = client.post("/v1/api/adt/encounters", headers=doctor_headers, json={"patient_id": p2["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1}).json()

    # 1. Overlapping time in the SAME theatre -> 409 Conflict
    overlap_ot = client.post(
        "/v1/api/ot/surgeries",
        headers=doctor_headers,
        json={
            "encounter_id": enc2["id"],
            "theatre_id": ot1["id"],
            "procedure_service_id": proc["id"],
            "lead_surgeon_id": 1,
            "scheduled_start_time": (start_t + timedelta(minutes=30)).isoformat(),
            "scheduled_end_time": (end_t + timedelta(minutes=30)).isoformat(),
        },
    )
    assert overlap_ot.status_code == 409
    assert "theatre" in overlap_ot.json()["message"].lower()

    # 2. Different theatre (ot2), but SAME Lead Surgeon (user 2) during overlapping window -> 409 Conflict
    overlap_doc = client.post(
        "/v1/api/ot/surgeries",
        headers=doctor_headers,
        json={
            "encounter_id": enc2["id"],
            "theatre_id": ot2["id"],
            "procedure_service_id": proc["id"],
            "lead_surgeon_id": 2,
            "scheduled_start_time": (start_t + timedelta(minutes=30)).isoformat(),
            "scheduled_end_time": (end_t + timedelta(minutes=30)).isoformat(),
        },
    )
    assert overlap_doc.status_code == 409
    assert "surgeon" in overlap_doc.json()["message"].lower()


def test_02_who_surgical_safety_checklist_hard_stop_before_incision_and_before_closure(
    client, superuser_headers, doctor_headers, nurse_headers
):
    """
    Scenario 2:
    - Starting surgery (IN_PROGRESS) without WHO Sign-In/Time-Out checklist is blocked with 400.
    - Completing surgery (COMPLETED) without sponge_instrument_count_verified (Sign-Out) is blocked with 400.
    """
    _, enc, ot, proc = _setup_ipd_surgical_case(client, superuser_headers, doctor_headers, ot_code="OT_WHO_1")
    surg = client.post(
        "/v1/api/ot/surgeries",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "theatre_id": ot["id"],
            "procedure_service_id": proc["id"],
            "lead_surgeon_id": 2,
            "scheduled_start_time": "2026-12-05T09:00:00Z",
            "scheduled_end_time": "2026-12-05T11:00:00Z",
        },
    ).json()

    # 1. Attempt to start surgery before WHO checklist -> 400
    assert client.patch(
        f"/v1/api/ot/surgeries/{surg['id']}/status",
        headers=doctor_headers,
        json={"status": "IN_PROGRESS"},
    ).status_code == 400

    # 2. Complete Pre-Op Sign-In & Time-Out (sponge count still False) -> Auto-transitions to PRE_OP_CLEARED
    chk_res = client.put(
        f"/v1/api/ot/surgeries/{surg['id']}/checklist",
        headers=nurse_headers,
        json={
            "patient_identity_confirmed": True,
            "site_marked": True,
            "consent_signed": True,
            "pulse_oximeter_functioning": True,
            "allergy_checked": True,
            "antibiotic_prophylaxis_given": True,
            "sponge_instrument_count_verified": False,
        },
    )
    assert chk_res.status_code == 200

    # 3. Start surgery (IN_PROGRESS) -> Succeeds (200)
    start_res = client.patch(
        f"/v1/api/ot/surgeries/{surg['id']}/status",
        headers=doctor_headers,
        json={"status": "IN_PROGRESS"},
    )
    assert start_res.status_code == 200
    assert start_res.json()["status"] == "IN_PROGRESS"

    # 4. Attempt to mark COMPLETED while sponge_instrument_count_verified is False -> Blocked with 400!
    assert client.patch(
        f"/v1/api/ot/surgeries/{surg['id']}/status",
        headers=doctor_headers,
        json={"status": "COMPLETED"},
    ).status_code == 400


def test_03_completing_surgery_auto_bills_procedure_ot_room_and_implants_and_prints_record(
    client, superuser_headers, doctor_headers, nurse_headers, billing_headers
):
    """
    Scenario 3: Full End-to-End Surgical Completion, Auto-Billing & Operative Report Printing:
    - Procedure Fee: ₹80,000 (8,000,000 paise)
    - OT Room Hourly Charge: ₹5,000/hr (500,000 paise)
    - Titanium Hip Implant: ₹45,000 (4,500,000 paise, S/N: ZIMMER-TI-2026-99)
    - Completing surgery auto-bills all 3 items (₹1,30,000 / 13,000,000 paise total) to the patient's DRAFT invoice
      and renders the printable Operative & Anesthesia Record with implant serial traceability!
    """
    p, enc, ot, proc = _setup_ipd_surgical_case(client, superuser_headers, doctor_headers, ot_code="OT_BILL_1")
    surg = client.post(
        "/v1/api/ot/surgeries",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "theatre_id": ot["id"],
            "procedure_service_id": proc["id"],
            "lead_surgeon_id": 2,
            "scheduled_start_time": "2026-12-10T08:00:00Z",
            "scheduled_end_time": "2026-12-10T09:00:00Z",
        },
    ).json()

    # Log Titanium Hip Prosthesis Implant (4,500,000 paise)
    imp_res = client.post(
        f"/v1/api/ot/surgeries/{surg['id']}/implants",
        headers=nurse_headers,
        json={
            "implant_name": "Uncemented Titanium Femoral Stem",
            "manufacturer": "Zimmer Biomet",
            "serial_or_batch_number": "ZIMMER-TI-2026-99",
            "quantity": 1,
            "unit_price_paise": 4500000,
        },
    )
    assert imp_res.status_code == 201

    # Complete WHO checklist (including sponge count) & start surgery
    client.put(
        f"/v1/api/ot/surgeries/{surg['id']}/checklist",
        headers=nurse_headers,
        json={
            "patient_identity_confirmed": True,
            "site_marked": True,
            "consent_signed": True,
            "pulse_oximeter_functioning": True,
            "allergy_checked": True,
            "antibiotic_prophylaxis_given": True,
            "sponge_instrument_count_verified": True,
        },
    )
    client.patch(f"/v1/api/ot/surgeries/{surg['id']}/status", headers=doctor_headers, json={"status": "IN_PROGRESS"})

    # Record Operative & Anesthesia Findings
    op_res = client.put(
        f"/v1/api/ot/surgeries/{surg['id']}/operative-record",
        headers=doctor_headers,
        json={
            "anesthesia_type": "SPINAL",
            "asa_physical_status": 2,
            "anesthesia_notes": "0.5% Heavy Bupivacaine at L3-L4",
            "operative_findings": "Severe osteoarthritic changes of right femoral head. Prosthesis seated with good stability.",
            "post_op_instructions": "DVT prophylaxis, limb abduction pillow, check X-ray tomorrow.",
            "estimated_blood_loss_ml": 250,
        },
    )
    assert op_res.status_code == 200

    # Complete Surgery -> Triggers auto_bill_completed_surgery!
    comp_res = client.patch(
        f"/v1/api/ot/surgeries/{surg['id']}/status",
        headers=doctor_headers,
        json={"status": "COMPLETED"},
    )
    assert comp_res.status_code == 200
    assert comp_res.json()["status"] == "COMPLETED"

    # Terminal state enforcement: cannot re-open or transition a COMPLETED surgery (400)
    terminal_res = client.patch(
        f"/v1/api/ot/surgeries/{surg['id']}/status",
        headers=doctor_headers,
        json={"status": "SCHEDULED"},
    )
    assert terminal_res.status_code == 400
    assert "terminal state" in terminal_res.json()["message"].lower()

    # Verify DRAFT invoice has Procedure (8,000,000) + OT Room (500,000) + Implant (4,500,000) = 13,000,000 paise (₹1,30,000)
    inv = client.get(f"/v1/api/billing/invoices?patient_id={p['id']}", headers=billing_headers).json()["items"][0]
    assert inv["total_amount_paise"] == 13000000
    assert len(inv["line_items"]) == 3

    # Verify tamper protection blocks manual deletion of surgical implant charge (403)
    implant_line_id = next(li["id"] for li in inv["line_items"] if "SURGICAL IMPLANT" in li["description"])
    assert client.delete(f"/v1/api/billing/invoices/{inv['id']}/items/{implant_line_id}", headers=superuser_headers).status_code == 403

    # Verify Printable Surgical Operative Note includes implant serial number and findings
    print_res = client.get(f"/v1/api/ot/surgeries/{surg['id']}/print", headers=doctor_headers)
    assert print_res.status_code == 200
    assert "SURGICAL &amp; ANESTHESIA OPERATIVE RECORD" in print_res.text
    assert "ZIMMER-TI-2026-99" in print_res.text
    assert "Severe osteoarthritic changes" in print_res.text