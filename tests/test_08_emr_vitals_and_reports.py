from datetime import datetime, timezone


def _create_active_opd_encounter(client, doctor_headers):
    p = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={"full_name": "EMR Chart Patient", "date_of_birth": "1988-05-10T00:00:00Z", "gender": "FEMALE"},
    ).json()
    enc = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={"patient_id": p["id"], "encounter_type": "OPD", "primary_doctor_id": 2, "department_id": 1},
    ).json()
    return p, enc


def test_01_record_and_list_encounter_vitals_with_auto_bmi(client, doctor_headers, nurse_headers):
    """Scenario 1: Nurse records vitals on an active encounter; BMI is auto-calculated from weight_kg and height_cm."""
    _, enc = _create_active_opd_encounter(client, doctor_headers)

    # 72 kg at 180 cm (1.8m) -> BMI = 72 / (1.8 * 1.8) = 22.22
    v_res = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/vitals",
        headers=nurse_headers,
        json={
            "temperature_f": 98.6,
            "pulse_bpm": 76,
            "bp_systolic": 120,
            "bp_diastolic": 80,
            "respiratory_rate": 16,
            "spo2_percent": 99,
            "weight_kg": 72.0,
            "height_cm": 180.0,
            "notes": "Resting vitals normal",
        },
    )
    assert v_res.status_code == 201
    assert v_res.json()["bmi"] == 22.22

    list_res = client.get(f"/v1/api/clinical/encounters/{enc['id']}/vitals", headers=doctor_headers)
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1


def test_02_block_vitals_and_notes_on_closed_encounter(client, superuser_headers, doctor_headers, nurse_headers):
    """Scenario 2: Recording vitals or SOAP notes on a CANCELLED encounter returns 400."""
    _, enc = _create_active_opd_encounter(client, doctor_headers)
    cancel_res = client.delete(f"/v1/api/adt/encounters/{enc['id']}", headers=superuser_headers)
    assert cancel_res.status_code == 200

    assert client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/vitals",
        headers=nurse_headers,
        json={"pulse_bpm": 80},
    ).status_code == 400

    assert client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/notes",
        headers=doctor_headers,
        json={"subjective_notes": "Patient complains of cough"},
    ).status_code == 400


def test_03_create_and_update_draft_soap_clinical_note_and_nurse_guard(client, doctor_headers, nurse_headers):
    """Scenario 3: Doctor creates and updates a draft SOAP note; Nurse is blocked from authoring notes (403)."""
    _, enc = _create_active_opd_encounter(client, doctor_headers)

    assert client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/notes",
        headers=nurse_headers,
        json={"subjective_notes": "Unauthorized nurse SOAP note"},
    ).status_code == 403

    note_res = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/notes",
        headers=doctor_headers,
        json={
            "subjective_notes": "3 days of high-grade fever and chills",
            "objective_notes": "Temp 101.4F, clear lungs",
            "assessment_notes": "Suspected viral pyrexia",
            "plan_notes": "Paracetamol 500mg TDS, CBC panel",
        },
    )
    assert note_res.status_code == 201
    note_id = note_res.json()["id"]

    upd_res = client.put(
        f"/v1/api/clinical/notes/{note_id}",
        headers=doctor_headers,
        json={"plan_notes": "Paracetamol 650mg TDS, CBC + Dengue NS1"},
    )
    assert upd_res.status_code == 200
    assert "Dengue NS1" in upd_res.json()["plan_notes"]


def test_04_attach_icd10_diagnoses_and_enforce_single_primary_diagnosis(client, doctor_headers):
    """Scenario 4: Attach PRIMARY and SECONDARY ICD-10 diagnoses; block a second PRIMARY diagnosis with 409."""
    _, enc = _create_active_opd_encounter(client, doctor_headers)

    d1 = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/diagnoses",
        headers=doctor_headers,
        json={"diagnosis_id": 1, "diagnosis_type": "PRIMARY", "clinical_remarks": "Confirmed primary diagnosis"},
    )
    assert d1.status_code == 201

    assert client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/diagnoses",
        headers=doctor_headers,
        json={"diagnosis_id": 2, "diagnosis_type": "PRIMARY"},
    ).status_code == 409

    d2 = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/diagnoses",
        headers=doctor_headers,
        json={"diagnosis_id": 2, "diagnosis_type": "SECONDARY", "clinical_remarks": "Co-existing condition"},
    )
    assert d2.status_code == 201


def test_05_sign_clinical_note_locks_against_future_edits(client, doctor_headers):
    """Scenario 5: Signing a SOAP note sets is_signed=True and blocks any subsequent PUT edits with 409."""
    _, enc = _create_active_opd_encounter(client, doctor_headers)
    note = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/notes",
        headers=doctor_headers,
        json={"subjective_notes": "Headache", "assessment_notes": "Tension headache", "plan_notes": "Rest"},
    ).json()

    sign_res = client.patch(f"/v1/api/clinical/notes/{note['id']}/sign", headers=doctor_headers)
    assert sign_res.status_code == 200
    assert sign_res.json()["is_signed"] is True

    edit_locked = client.put(
        f"/v1/api/clinical/notes/{note['id']}",
        headers=doctor_headers,
        json={"plan_notes": "Tampering with signed note"},
    )
    assert edit_locked.status_code == 409
    assert "medico-legal lock" in edit_locked.json()["message"].lower()


def test_06_print_opd_appointment_case_sheet_with_bmi_and_doctor_writing_space(client, doctor_headers, nurse_headers):
    """Scenario 6: OPD Appointment Case Sheet prints BMI/Vitals strip and blank handwriting canvas for the doctor."""
    p, enc = _create_active_opd_encounter(client, doctor_headers)

    # Record vitals (81 kg, 180 cm -> BMI = 25.0)
    client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/vitals",
        headers=nurse_headers,
        json={"weight_kg": 81.0, "height_cm": 180.0, "bp_systolic": 118, "bp_diastolic": 78, "pulse_bpm": 72, "spo2_percent": 98},
    )

    # Book OPD appointment and link to encounter
    appt = client.post(
        "/v1/api/opd/appointments",
        headers=doctor_headers,
        json={"patient_id": p["id"], "doctor_id": 2, "department_id": 1, "appointment_time": "2026-10-20T11:00:00Z", "reason_for_visit": "Annual health check & BMI review"},
    ).json()
    client.patch(f"/v1/api/opd/appointments/{appt['id']}/status", headers=doctor_headers, json={"status": "ARRIVED"})
    client.patch(f"/v1/api/opd/appointments/{appt['id']}/status", headers=doctor_headers, json={"status": "IN_CONSULTATION", "encounter_id": enc["id"]})

    print_res = client.get(f"/v1/api/opd/appointments/{appt['id']}/print", headers=doctor_headers)
    assert print_res.status_code == 200
    assert "OPD CONSULTATION CASE SHEET" in print_res.text
    assert "25.0 kg/m²" in print_res.text
    assert "writing-canvas" in print_res.text
    assert "Doctor's Signature" in print_res.text


def test_07_generate_printable_invoice_and_switch_to_thermal_80mm_template(client, superuser_headers, billing_headers):
    """Scenario 7: Print invoice in standard A4 default, then dynamically switch to SYS_INV_THERMAL_80MM."""
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_PRT", "name": "Print Cat"}).json()
    svc = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "SVC_PRT", "name": "Consultation Charge", "service_type": "CONSULTATION", "base_price_paise": 50000, "is_order_set": False}).json()
    p = client.post("/v1/api/patients", headers=superuser_headers, json={"full_name": "Print Inv Patient", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"}).json()

    inv = client.post("/v1/api/billing/invoices", headers=billing_headers, json={"patient_id": p["id"]}).json()
    client.post(f"/v1/api/billing/invoices/{inv['id']}/items", headers=billing_headers, json={"service_id": svc["id"], "description": "Consultation", "quantity": 1, "unit_price_paise": 50000})
    client.patch(f"/v1/api/billing/invoices/{inv['id']}/finalize", headers=billing_headers)
    client.post(f"/v1/api/billing/invoices/{inv['id']}/payments", headers=billing_headers, json={"amount_paid_paise": 50000, "payment_method": "UPI"})

    # 1. Default A4 Invoice
    a4_res = client.get(f"/v1/api/billing/invoices/{inv['id']}/print", headers=billing_headers)
    assert a4_res.status_code == 200
    assert "TAX INVOICE &amp; PAYMENT RECEIPT" in a4_res.text
    assert "₹500.00" in a4_res.text
    standard_pdf = client.get(f"/v1/api/billing/invoices/{inv['id']}/pdf", headers=billing_headers)
    assert standard_pdf.status_code == 200
    assert standard_pdf.headers["content-type"] == "application/pdf"
    assert standard_pdf.content.startswith(b"%PDF-")

    profile_res = client.put(
        "/v1/api/admin/policies",
        headers=superuser_headers,
        json={
            "invoice_document_profile": {
                "legal_entity_name": "Avinya US Health LLC",
                "address_lines": ["100 Main Street", "San Francisco, CA"],
                "currency_code": "USD",
                "currency_locale": "en_US",
                "minor_unit_digits": 2,
                "tax_registrations": [
                    {"jurisdiction": "US-CA", "label": "Tax ID", "registration_number": "TAX-123"}
                ],
                "tax_note": "Configured for local tax review.",
                "payment_terms": "Due on receipt.",
            }
        },
    )
    assert profile_res.status_code == 200
    customized_html = client.get(f"/v1/api/billing/invoices/{inv['id']}/print", headers=billing_headers)
    assert "Avinya US Health LLC" in customized_html.text
    assert "TAX-123" in customized_html.text
    assert "USD" in customized_html.text
    assert "$500.00" in customized_html.text

    # 2. Switch on-the-fly to 80mm Thermal POS Receipt
    thermal_res = client.get(
        f"/v1/api/billing/invoices/{inv['id']}/print?template_code=SYS_INV_THERMAL_80MM",
        headers=billing_headers,
    )
    assert thermal_res.status_code == 200
    assert "THERMAL POS RECEIPT" in thermal_res.text
    assert "width: 300px" in thermal_res.text
    thermal_pdf = client.get(
        f"/v1/api/billing/invoices/{inv['id']}/pdf?template_code=SYS_INV_THERMAL_80MM",
        headers=billing_headers,
    )
    assert thermal_pdf.status_code == 200
    assert thermal_pdf.content.startswith(b"%PDF-")


def test_08_generate_printable_erx_with_doctor_nmc_and_allergy_banner(client, doctor_headers, pharmacist_headers):
    """Scenario 8: Printable eRx includes doctor's NMC registration number and patient allergy banner."""
    med = client.post(
        "/v1/api/pharmacy/medications",
        headers=pharmacist_headers,
        json={"code": "MED_CET_10", "brand_name": "Cetzine", "generic_name": "Cetirizine", "form": "TABLET", "strength": "10mg"},
    ).json()

    p, enc = _create_active_opd_encounter(client, doctor_headers)
    client.post(f"/v1/api/patients/{p['id']}/allergies", headers=doctor_headers, json={"allergy_id": 2, "severity": "SEVERE", "reaction": "Rash"})

    rx = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={"encounter_id": enc["id"], "clinical_notes": "Take at bedtime", "items": [{"medication_id": med["id"], "dosage_instruction": "0-0-1 HS", "requested_quantity": 5}]},
    ).json()

    print_res = client.get(f"/v1/api/pharmacy/prescriptions/{rx['id']}/print", headers=doctor_headers)
    assert print_res.status_code == 200
    assert "NMC-2026-001" in print_res.text
    assert "Sulfonamides" in print_res.text
    assert "Cetzine" in print_res.text


def test_09_diagnostic_report_blocks_unverified_results_from_printing(client, superuser_headers, doctor_headers):
    """Scenario 9: Printing a lab order before all tasks are VERIFIED returns 409; succeeds once VERIFIED."""
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_LABP", "name": "Lab Print"}).json()
    svc = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "LAB_HB", "name": "Hemoglobin", "service_type": "LAB", "base_price_paise": 15000, "is_order_set": False}).json()
    p, _ = _create_active_opd_encounter(client, doctor_headers)

    order = client.post("/v1/api/diagnostics/orders", headers=doctor_headers, json={"patient_id": p["id"], "ordered_service_ids": [svc["id"]]}).json()
    task_id = order["tasks"][0]["id"]

    assert client.get(f"/v1/api/diagnostics/orders/{order['id']}/print", headers=doctor_headers).status_code == 409

    client.patch(f"/v1/api/diagnostics/tasks/{task_id}/result", headers=doctor_headers, json={"result_value": "14.2 g/dL", "status": "RESULT_ENTERED"})
    assert client.get(f"/v1/api/diagnostics/orders/{order['id']}/print", headers=doctor_headers).status_code == 409

    client.patch(f"/v1/api/diagnostics/tasks/{task_id}/result", headers=superuser_headers, json={"result_value": "14.2 g/dL", "status": "VERIFIED"})
    print_res = client.get(f"/v1/api/diagnostics/orders/{order['id']}/print", headers=doctor_headers)
    assert print_res.status_code == 200
    assert "14.2 g/dL" in print_res.text


def test_10_generate_printable_ipd_discharge_summary(client, superuser_headers, doctor_headers):
    """Scenario 10: Printable IPD discharge summary blocks active admissions (400) and prints once discharged (200)."""
    f_id = client.post("/v1/api/ip/floors", headers=superuser_headers, json={"floor_number": 5, "name": "Fifth Floor"}).json()["floor_id"]
    w_id = client.post("/v1/api/ip/wards", headers=superuser_headers, json={"floor_id": f_id, "department_id": 1, "code": "W-DISC", "name": "Discharge Ward", "ward_type": "GENERAL"}).json()["id"]
    r_id = client.post("/v1/api/ip/rooms", headers=superuser_headers, json={"ward_id": w_id, "room_number": "501", "room_type": "SINGLE", "bed_capacity": 1}).json()["room_id"]
    b_id = client.post("/v1/api/ip/beds", headers=superuser_headers, json={"room_id": r_id, "bed_number": "501-A", "bed_type": "STANDARD", "base_daily_rate_paise": 200000}).json()["bed_id"]

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "IPD Summary Patient", "date_of_birth": "1978-03-15T00:00:00Z", "gender": "MALE"}).json()
    enc = client.post("/v1/api/adt/encounters", headers=doctor_headers, json={"patient_id": p["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1}).json()
    adm = client.post("/v1/api/adt/admissions", headers=doctor_headers, json={"encounter_id": enc["id"], "admission_type": "ROUTINE", "admitting_doctor_id": 2, "assigned_bed_id": b_id}).json()

    client.post(f"/v1/api/clinical/encounters/{enc['id']}/diagnoses", headers=doctor_headers, json={"diagnosis_id": 1, "diagnosis_type": "PRIMARY"})
    note = client.post(f"/v1/api/clinical/encounters/{enc['id']}/notes", headers=doctor_headers, json={"assessment_notes": "Community acquired pneumonia", "plan_notes": "IV antibiotics completed"}).json()
    client.patch(f"/v1/api/clinical/notes/{note['id']}/sign", headers=doctor_headers)

    assert client.get(f"/v1/api/adt/admissions/{adm['id']}/discharge-summary/print", headers=doctor_headers).status_code == 400

    client.post(
        f"/v1/api/adt/admissions/{adm['id']}/discharge",
        headers=superuser_headers,
        json={"discharge_disposition": "ROUTINE_DISCHARGE", "discharge_summary": "Afebrile for 48 hours. Follow up in OPD after 7 days."},
    )
    print_res = client.get(f"/v1/api/adt/admissions/{adm['id']}/discharge-summary/print", headers=doctor_headers)
    assert print_res.status_code == 200
    assert "INPATIENT CLINICAL DISCHARGE SUMMARY" in print_res.text
    assert "Community acquired pneumonia" in print_res.text


def test_11_fetch_draggable_variable_catalog_for_template_builder(client, doctor_headers):
    """Scenario 11: GET /api/reports/templates/variables/OPD_APPOINTMENT_SLIP returns BMI and vitals tokens."""
    res = client.get("/v1/api/reports/templates/variables/OPD_APPOINTMENT_SLIP", headers=doctor_headers)
    assert res.status_code == 200
    groups = res.json()["groups"]
    vitals_group = next(g for g in groups if "BMI" in g["group_name"])
    tokens = [v["token"] for v in vitals_group["variables"]]
    assert "{{ vitals.bmi }}" in tokens
    assert "{{ vitals.weight_kg }}" in tokens


def test_12_system_template_is_read_only_until_forked_and_customized(client, superuser_headers, doctor_headers):
    """Scenario 12: System templates block direct PUT edits (400), but can be forked, repositioned, and set as default."""
    templates = client.get("/v1/api/reports/templates?report_type=OPD_APPOINTMENT_SLIP", headers=superuser_headers).json()
    sys_tpl = next(t for t in templates if t["code"] == "SYS_OPD_CASE_SHEET_A4")

    # 1. Direct modification of a system standard template is blocked with 400
    assert client.put(
        f"/v1/api/reports/templates/{sys_tpl['id']}",
        headers=superuser_headers,
        json={"name": "Overwrite Attempt"},
    ).status_code == 400

    # 2. Fork/Import SYS_OPD_CASE_SHEET_A4 into a custom hospital template
    fork_res = client.post(
        "/v1/api/reports/templates/fork/SYS_OPD_CASE_SHEET_A4",
        headers=superuser_headers,
        json={"new_code": "APOLLO_OPD_CUSTOM_V1", "new_name": "Apollo Custom Letterhead OPD Sheet", "set_as_default": True},
    )
    assert fork_res.status_code == 201
    custom_tpl = fork_res.json()
    assert custom_tpl["is_default"] is True
    assert custom_tpl["is_system_template"] is False

    # 3. Adjust drag-and-drop widget coordinates in layout_schema and customize HTML/CSS
    updated_layout = custom_tpl["layout_schema"]
    updated_layout["widgets"][0]["y"] = 4  # Move header down 40mm for pre-printed letterhead
    upd_res = client.put(
        f"/v1/api/reports/templates/{custom_tpl['id']}",
        headers=superuser_headers,
        json={
            "layout_schema": updated_layout,
            "header_html": "<h1>CUSTOM LETTERHEAD — {{ tenant.hospital_name }}</h1>",
            "html_template": "<div>Custom OPD BMI Box: {{ vitals.bmi }} | Patient: {{ patient.full_name }}</div><div style='height:600px;'>Doctor Blank Space</div>",
        },
    )
    assert upd_res.status_code == 200
    assert upd_res.json()["layout_schema"]["widgets"][0]["y"] == 4

    # 4. Printing an OPD appointment now automatically uses the new customized default template!
    p, enc = _create_active_opd_encounter(client, doctor_headers)
    appt = client.post(
        "/v1/api/opd/appointments",
        headers=doctor_headers,
        json={"patient_id": p["id"], "doctor_id": 2, "department_id": 1, "appointment_time": "2026-10-22T10:00:00Z"},
    ).json()
    print_res = client.get(f"/v1/api/opd/appointments/{appt['id']}/print", headers=doctor_headers)
    assert print_res.status_code == 200
    assert "CUSTOM LETTERHEAD" in print_res.text
    assert "Custom OPD BMI Box" in print_res.text


def test_hospital_can_select_designed_or_preprinted_report_variant(
    client, superuser_headers, doctor_headers
):
    source = client.get(
        "/v1/api/reports/templates?report_type=OPD_APPOINTMENT_SLIP",
        headers=superuser_headers,
    ).json()
    designed_template = next(
        item for item in source if item["code"] == "SYS_OPD_CASE_SHEET_A4"
    )
    assert designed_template["print_mode"] == "DESIGNED"

    fork = client.post(
        "/v1/api/reports/templates/fork/SYS_OPD_CASE_SHEET_A4",
        headers=superuser_headers,
        json={
            "new_code": "CUSTOM_OPD_PREPRINTED",
            "new_name": "Preprinted OPD stationery",
            "print_mode": "PREPRINTED_DATA_ONLY",
            "set_as_default": True,
        },
    )
    assert fork.status_code == 201, fork.text
    data_template_id = fork.json()["id"]
    updated = client.put(
        f"/v1/api/reports/templates/{data_template_id}",
        headers=superuser_headers,
        json={
            "html_template": (
                "<main>{{ patient.full_name }}|{{ patient.mrn }}|"
                "{{ appointment.appointment_time }}</main>"
            ),
            "css_styles": "main { position: absolute; top: 20mm; left: 30mm; }",
            "page_size": "A5_PORTRAIT",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["print_mode"] == "PREPRINTED_DATA_ONLY"

    configured = client.put(
        "/v1/api/admin/policies",
        headers=superuser_headers,
        json={
            "report_print_modes": {
                "OPD_APPOINTMENT_SLIP": "PREPRINTED_DATA_ONLY"
            }
        },
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["report_print_modes"]["OPD_APPOINTMENT_SLIP"] == (
        "PREPRINTED_DATA_ONLY"
    )
    assert client.put(
        "/v1/api/admin/policies",
        headers=superuser_headers,
        json={
            "report_print_modes": {
                "DIAGNOSTIC_REPORT": "PREPRINTED_DATA_ONLY"
            }
        },
    ).status_code == 409

    patient, _ = _create_active_opd_encounter(client, doctor_headers)
    appointment = client.post(
        "/v1/api/opd/appointments",
        headers=doctor_headers,
        json={
            "patient_id": patient["id"],
            "doctor_id": 2,
            "department_id": 1,
            "appointment_time": "2026-10-22T10:00:00Z",
        },
    ).json()

    default_print = client.get(
        f"/v1/api/opd/appointments/{appointment['id']}/print",
        headers=doctor_headers,
    )
    assert default_print.status_code == 200, default_print.text
    assert "Preprinted OPD stationery" not in default_print.text
    assert "position: absolute; top: 20mm" in default_print.text
    assert "@page { size: A5 portrait" in default_print.text
    assert patient["full_name"] in default_print.text
    assert "CUSTOM LETTERHEAD" not in default_print.text
    assert "Printed:" not in default_print.text

    designed_override = client.get(
        f"/v1/api/opd/appointments/{appointment['id']}/print?print_mode=DESIGNED",
        headers=doctor_headers,
    )
    assert designed_override.status_code == 200
    assert "OPD CONSULTATION CASE SHEET" in designed_override.text

    mismatch = client.get(
        f"/v1/api/opd/appointments/{appointment['id']}/print"
        "?template_code=SYS_OPD_CASE_SHEET_A4&print_mode=PREPRINTED_DATA_ONLY",
        headers=doctor_headers,
    )
    assert mismatch.status_code == 422


def test_appointment_status_transitions_are_audited_and_browsable(
    client, doctor_headers
):
    today = datetime.now(timezone.utc).date()
    patient, _ = _create_active_opd_encounter(client, doctor_headers)
    appointment = client.post(
        "/v1/api/opd/appointments",
        headers=doctor_headers,
        json={
            "patient_id": patient["id"],
            "doctor_id": 2,
            "department_id": 1,
            "appointment_time": datetime.combine(
                today, datetime.min.time(), tzinfo=timezone.utc
            ).isoformat(),
        },
    )
    assert appointment.status_code == 201, appointment.text
    appointment_id = appointment.json()["id"]

    updated = client.patch(
        f"/v1/api/opd/appointments/{appointment_id}/status",
        headers=doctor_headers,
        json={"status": "ARRIVED", "reason": "Patient checked in"},
    )
    assert updated.status_code == 200, updated.text

    history = client.get(
        f"/v1/api/opd/appointments/{appointment_id}/status-history",
        headers=doctor_headers,
    )
    assert history.status_code == 200, history.text
    entries = history.json()
    assert [entry["to_status"] for entry in entries] == ["SCHEDULED", "ARRIVED"]
    assert entries[0]["from_status"] is None
    assert entries[1]["from_status"] == "SCHEDULED"
    assert entries[1]["reason"] == "Patient checked in"
    assert entries[1]["changed_by_id"] is not None
    performance = client.get(
        "/v1/api/analytics/appointment-performance",
        headers=doctor_headers,
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
    )
    assert performance.status_code == 200, performance.text
    summary = performance.json()
    transitions = {
        item["status"]: item["count"]
        for item in summary["recorded_status_transition_counts"]
    }
    assert transitions["ARRIVED"] == 1
    assert summary["appointments_with_observed_status_transitions"] == 1


def test_13_create_template_from_scratch_and_switch_default(client, superuser_headers):
    """Scenario 13: Admin creates a brand-new template from scratch and promotes it via PATCH /set-default."""
    create_res = client.post(
        "/v1/api/reports/templates",
        headers=superuser_headers,
        json={
            "report_type": "DIAGNOSTIC_REPORT",
            "code": "CUSTOM_LAB_COMPACT_A5",
            "name": "Compact A5 Lab Slip",
            "page_size": "A5_PORTRAIT",
            "layout_schema": {"canvas": {"width_mm": 148, "height_mm": 210}, "widgets": [{"id": "w1", "x": 0, "y": 0}]},
            "html_template": "<div>Compact Lab Report for {{ patient.full_name }}</div>",
            "is_default": False,
        },
    )
    assert create_res.status_code == 201
    tpl_id = create_res.json()["id"]

    # Promote to default
    prom_res = client.patch(f"/v1/api/reports/templates/{tpl_id}/set-default", headers=superuser_headers)
    assert prom_res.status_code == 200
    assert prom_res.json()["is_default"] is True


def test_14_live_template_preview_renders_sample_context(client, superuser_headers):
    """Scenario 14: POST /api/reports/templates/preview renders draft HTML with realistic sample data."""
    prev_res = client.post(
        "/v1/api/reports/templates/preview",
        headers=superuser_headers,
        json={
            "report_type": "OPD_APPOINTMENT_SLIP",
            "header_html": "<h2>Preview Header: {{ tenant.branch_code }}</h2>",
            "html_template": "<p>Preview Patient: {{ patient.full_name }} | Sample BMI: {{ vitals.bmi }}</p>",
        },
    )
    assert prev_res.status_code == 200
    assert "Preview Header: HQ-HYD-01" in prev_res.text
    assert "Sample BMI: 22.22 kg/m²" in prev_res.text
    data_only_preview = client.post(
        "/v1/api/reports/templates/preview",
        headers=superuser_headers,
        json={
            "report_type": "OPD_APPOINTMENT_SLIP",
            "print_mode": "PREPRINTED_DATA_ONLY",
            "page_size": "A5_PORTRAIT",
            "header_html": "<h2>Must not print on preprinted paper</h2>",
            "html_template": "<p>{{ patient.full_name }}</p>",
        },
    )
    assert data_only_preview.status_code == 200, data_only_preview.text
    assert "Must not print on preprinted paper" not in data_only_preview.text
    assert "@page { size: A5 portrait; margin: 0; }" in data_only_preview.text


def test_custom_paper_profile_persists_renders_forks_and_can_be_cleared(
    client, superuser_headers
):
    created = client.post(
        "/v1/api/reports/templates",
        headers=superuser_headers,
        json={
            "report_type": "INVOICE",
            "code": "CUSTOM_INVOICE_STOCK",
            "name": "Custom invoice stock",
            "paper_width_mm": 210,
            "paper_height_mm": 280,
            "page_margin_top_mm": 4,
            "page_margin_right_mm": 5,
            "page_margin_bottom_mm": 6,
            "page_margin_left_mm": 7,
            "content_offset_x_mm": 1.5,
            "content_offset_y_mm": -2,
            "layout_schema": {},
            "html_template": "<main>Invoice</main>",
        },
    )
    assert created.status_code == 201, created.text
    template = created.json()
    assert template["paper_width_mm"] == 210
    assert template["page_margin_left_mm"] == 7

    forked = client.post(
        "/v1/api/reports/templates/fork/CUSTOM_INVOICE_STOCK",
        headers=superuser_headers,
        json={"new_code": "CUSTOM_INVOICE_STOCK_FORK", "new_name": "Forked invoice"},
    )
    assert forked.status_code == 201, forked.text
    assert forked.json()["paper_height_mm"] == 280
    assert forked.json()["content_offset_y_mm"] == -2

    preview = client.post(
        "/v1/api/reports/templates/preview",
        headers=superuser_headers,
        json={
            "report_type": "INVOICE",
            "paper_width_mm": 210,
            "paper_height_mm": 280,
            "page_margin_top_mm": 4,
            "page_margin_right_mm": 5,
            "page_margin_bottom_mm": 6,
            "page_margin_left_mm": 7,
            "content_offset_x_mm": 1.5,
            "content_offset_y_mm": -2,
            "html_template": "<main>Preview</main>",
        },
    )
    assert preview.status_code == 200, preview.text
    assert "@page { size: 210mm 280mm; margin: 4mm 5mm 6mm 7mm; }" in preview.text
    assert "left: 1.5mm; top: -2mm;" in preview.text

    cleared = client.put(
        f"/v1/api/reports/templates/{template['id']}",
        headers=superuser_headers,
        json={
            "paper_width_mm": None,
            "paper_height_mm": None,
            "page_margin_top_mm": None,
            "page_margin_right_mm": None,
            "page_margin_bottom_mm": None,
            "page_margin_left_mm": None,
            "content_offset_x_mm": None,
            "content_offset_y_mm": None,
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["paper_width_mm"] is None
    assert cleared.json()["content_offset_y_mm"] is None


def test_custom_paper_profile_rejects_incomplete_or_unprintable_dimensions(
    client, superuser_headers
):
    incomplete = client.post(
        "/v1/api/reports/templates",
        headers=superuser_headers,
        json={
            "report_type": "INVOICE",
            "code": "INVALID_PAPER_PROFILE",
            "name": "Invalid paper profile",
            "paper_width_mm": 210,
            "layout_schema": {},
            "html_template": "<main>Invoice</main>",
        },
    )
    assert incomplete.status_code == 422

    unprintable = client.post(
        "/v1/api/reports/templates/preview",
        headers=superuser_headers,
        json={
            "report_type": "INVOICE",
            "paper_width_mm": 100,
            "paper_height_mm": 100,
            "page_margin_top_mm": 50,
            "page_margin_bottom_mm": 50,
            "html_template": "<main>Preview</main>",
        },
    )
    assert unprintable.status_code == 422


def test_15_sandboxed_jinja2_blocks_ssti_code_execution_attempts(client, superuser_headers):
    """Scenario 15: Malicious SSTI payloads (e.g., accessing __class__) are blocked with 400 by SandboxedEnvironment."""
    ssti_res = client.post(
        "/v1/api/reports/templates/preview",
        headers=superuser_headers,
        json={
            "report_type": "INVOICE",
            "html_template": "Malicious payload: {{ ''.__class__.__mro__[1].__subclasses__() }}",
        },
    )
    assert ssti_res.status_code == 400
    assert "security violation" in ssti_res.json()["message"].lower()

def test_16_expanded_variable_catalog_covers_all_7_report_types_and_renders_invoice_discounts_and_claims(
    client, superuser_headers, doctor_headers, billing_headers
):
    """
    Scenario 16: Verify GET /api/reports/templates/variables/{report_type} exposes draggable tokens
    for all 7 ReportTypeEnum types (including MLC_POLICE_REPORT, SURGICAL_OPERATIVE_NOTE, Invoice Discounts/Claims,
    and Radiology PACS DICOM fields), and that printing a discounted invoice renders the net payable & discount reason.
    """
    for r_type in [
        "OPD_APPOINTMENT_SLIP",
        "INVOICE",
        "PRESCRIPTION",
        "DIAGNOSTIC_REPORT",
        "DISCHARGE_SUMMARY",
        "MLC_POLICE_REPORT",
        "SURGICAL_OPERATIVE_NOTE",
    ]:
        res = client.get(f"/v1/api/reports/templates/variables/{r_type}", headers=doctor_headers)
        assert res.status_code == 200
        assert len(res.json()["groups"]) >= 2

    # Verify INVOICE variable catalog includes discount and insurance_claim tokens
    inv_vars = client.get("/v1/api/reports/templates/variables/INVOICE", headers=billing_headers).json()["groups"]
    all_inv_tokens = [v["token"] for g in inv_vars for v in g.get("variables", [])]
    assert "{{ invoice.discount_inr }}" in all_inv_tokens
    assert "{{ invoice.net_inr }}" in all_inv_tokens
    assert "{{ insurance_claim.insurer_payable_inr }}" in all_inv_tokens

    # Create a discounted invoice and verify /print renders the discount and net payable
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_RPT16", "name": "Report 16"}).json()
    svc = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "SVC_RPT16", "name": "Full Body Checkup", "service_type": "PACKAGE", "base_price_paise": 200000, "is_order_set": False}).json()
    p, _ = _create_active_opd_encounter(client, doctor_headers)

    inv = client.post("/v1/api/billing/invoices", headers=billing_headers, json={"patient_id": p["id"]}).json()
    client.post(f"/v1/api/billing/invoices/{inv['id']}/items", headers=billing_headers, json={"service_id": svc["id"], "description": "Checkup", "quantity": 1, "unit_price_paise": 200000})
    client.patch(f"/v1/api/billing/invoices/{inv['id']}/finalize", headers=billing_headers)
    client.patch(
        f"/v1/api/billing/invoices/{inv['id']}/discount",
        headers=superuser_headers,
        json={"discount_amount_paise": 50000, "discount_reason": "Staff Family 25% Waiver"},
    )

    print_res = client.get(f"/v1/api/billing/invoices/{inv['id']}/print", headers=billing_headers)
    assert print_res.status_code == 200
    assert "Staff Family 25% Waiver" in print_res.text
    assert "₹1,500.00" in print_res.text