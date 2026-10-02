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
    data = v_res.json()
    assert data["bmi"] == 22.22

    list_res = client.get(f"/v1/api/clinical/encounters/{enc['id']}/vitals", headers=doctor_headers)
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1


def test_02_block_vitals_and_notes_on_closed_encounter(client, multi_tenant_env, doctor_headers, nurse_headers):
    """Scenario 2: Recording vitals or SOAP notes on a CANCELLED or DISCHARGED encounter returns 400."""
    from sqlalchemy.orm import sessionmaker
    from models import EncounterStatusEnum, PatientEncounter

    _, enc = _create_active_opd_encounter(client, doctor_headers)
    client.delete(f"/v1/api/adt/encounters/{enc['id']}", headers=doctor_headers)
    
    # Mark the encounter as DISCHARGED
    DefaultSession = sessionmaker(bind=multi_tenant_env["tenant_engines"]["hms_default"])
    with DefaultSession() as db:
        db_enc = db.get(PatientEncounter, enc["id"])
        db_enc.status = EncounterStatusEnum.DISCHARGED
        db.commit()

    v_res = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/vitals",
        headers=nurse_headers,
        json={"pulse_bpm": 80},
    )
    assert v_res.status_code == 400

    n_res = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/notes",
        headers=doctor_headers,
        json={"subjective_notes": "Patient complains of cough"},
    )
    assert n_res.status_code == 400


def test_03_create_and_update_draft_soap_clinical_note_and_nurse_guard(client, doctor_headers, nurse_headers):
    """Scenario 3: Doctor creates and updates a draft SOAP note; Nurse (lacks opd:write) is blocked from authoring notes (403)."""
    _, enc = _create_active_opd_encounter(client, doctor_headers)

    # Nurse cannot author SOAP clinical notes (403)
    assert client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/notes",
        headers=nurse_headers,
        json={"subjective_notes": "Unauthorized nurse SOAP note"},
    ).status_code == 403

    # Doctor creates draft SOAP note
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
    assert note_res.json()["is_signed"] is False

    # Doctor updates draft SOAP note before signing
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

    # Attach first PRIMARY diagnosis (ID 1 from seeded ICD-10 dictionary)
    d1 = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/diagnoses",
        headers=doctor_headers,
        json={"diagnosis_id": 1, "diagnosis_type": "PRIMARY", "clinical_remarks": "Confirmed primary diagnosis"},
    )
    assert d1.status_code == 201
    assert d1.json()["diagnosis_type"] == "PRIMARY"

    # Attempting to attach a SECOND PRIMARY diagnosis to the same encounter returns 409 Conflict
    d_dup_primary = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/diagnoses",
        headers=doctor_headers,
        json={"diagnosis_id": 2, "diagnosis_type": "PRIMARY"},
    )
    assert d_dup_primary.status_code == 409

    # Attaching ID 2 as SECONDARY succeeds
    d2 = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/diagnoses",
        headers=doctor_headers,
        json={"diagnosis_id": 2, "diagnosis_type": "SECONDARY", "clinical_remarks": "Co-existing condition"},
    )
    assert d2.status_code == 201

    diag_list = client.get(f"/v1/api/clinical/encounters/{enc['id']}/diagnoses", headers=doctor_headers).json()
    assert len(diag_list) == 2


def test_05_sign_clinical_note_locks_against_future_edits(client, doctor_headers):
    """Scenario 5: Signing a SOAP note sets is_signed=True and blocks any subsequent PUT edits with 409."""
    _, enc = _create_active_opd_encounter(client, doctor_headers)
    note = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/notes",
        headers=doctor_headers,
        json={"subjective_notes": "Headache", "assessment_notes": "Tension headache", "plan_notes": "Rest"},
    ).json()

    # Sign off the note
    sign_res = client.patch(f"/v1/api/clinical/notes/{note['id']}/sign", headers=doctor_headers)
    assert sign_res.status_code == 200
    assert sign_res.json()["is_signed"] is True
    assert sign_res.json()["signed_at"] is not None

    # Re-signing an already signed note returns 409
    assert client.patch(f"/v1/api/clinical/notes/{note['id']}/sign", headers=doctor_headers).status_code == 409

    # Attempting to edit a signed note returns 409 Medico-Legal Lock
    edit_locked = client.put(
        f"/v1/api/clinical/notes/{note['id']}",
        headers=doctor_headers,
        json={"plan_notes": "Tampering with signed note"},
    )
    assert edit_locked.status_code == 409
    assert "medico-legal lock" in edit_locked.json()["message"].lower()