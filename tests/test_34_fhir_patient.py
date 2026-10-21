from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.domains.clinical.models import DiagnosisTypeEnum, EncounterDiagnosis, EncounterVital
from backend.domains.encounters.models import EncounterStatusEnum, EncounterTypeEnum, PatientEncounter
from backend.domains.identity.models import Department, GenderEnum, User
from backend.domains.patients.models import DiagnosisMaster, Patient


def test_fhir_patient_read_and_search_are_tenant_scoped(
    client, multi_tenant_env, apollo_admin_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_apollo"]
    with Session(engine) as db:
        patient = Patient(
            mrn="FHIR-001",
            full_name="Asha Rao",
            date_of_birth=datetime(1990, 5, 1, tzinfo=timezone.utc),
            gender=GenderEnum.FEMALE,
            phone_number="+919999000001",
            email="asha@example.invalid",
            address="12 Lake Road",
            village="Rangapur",
            city="Hyderabad",
            district="Rangareddy",
            state="Telangana",
            pincode="500001",
        )
        db.add(patient)
        db.commit()
        patient_id = patient.id

    headers = apollo_admin_headers
    metadata = client.get("/v1/api/fhir/R4/metadata")
    assert metadata.status_code == 200
    assert metadata.headers["content-type"].startswith("application/fhir+json")
    meta_json = metadata.json()
    assert meta_json["fhirVersion"] == "4.0.1"
    res_types = [r["type"] for r in meta_json["rest"][0]["resource"]]
    assert res_types == ["Patient", "Encounter", "Observation", "Condition"]

    read = client.get(f"/v1/api/fhir/R4/Patient/{patient_id}", headers=headers)
    assert read.status_code == 200, read.text
    resource = read.json()
    assert resource["resourceType"] == "Patient"
    assert resource["id"] == str(patient_id)
    assert resource["gender"] == "female"
    assert resource["birthDate"] == "1990-05-01"
    assert resource["identifier"][0] == {
        "use": "usual",
        "system": "urn:avinya:hms:mrn",
        "value": "FHIR-001",
    }
    assert resource["address"][0]["district"] == "Rangareddy"

    search = client.get(
        "/v1/api/fhir/R4/Patient?name=Asha&identifier=urn%3Aavinya%3Ahms%3Amrn%7CFHIR-001",
        headers=headers,
    )
    assert search.status_code == 200, search.text
    bundle = search.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["total"] == 1
    assert bundle["entry"][0]["resource"]["id"] == str(patient_id)
    wildcard_search = client.get(
        "/v1/api/fhir/R4/Patient",
        params={"name": "A%"},
        headers=headers,
    )
    assert wildcard_search.status_code == 200
    assert wildcard_search.json()["total"] == 0
    not_found = client.get(
        "/v1/api/fhir/R4/Patient/999999",
        headers=headers,
    )
    assert not_found.status_code == 404
    assert not_found.headers["content-type"].startswith("application/fhir+json")
    assert not_found.json()["resourceType"] == "OperationOutcome"
    assert client.get(
        f"/v1/api/fhir/R4/Patient/{patient_id}",
        headers={"X-Hospital-Subdomain": "default", "Authorization": headers["Authorization"]},
    ).status_code == 401


def test_fhir_encounter_observation_condition_read_and_search(
    client, multi_tenant_env, apollo_admin_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_apollo"]
    now = datetime.now(timezone.utc)

    with Session(engine) as db:
        patient = Patient(
            mrn="FHIR-002",
            full_name="Vikram Seth",
            date_of_birth=datetime(1985, 8, 15, tzinfo=timezone.utc),
            gender=GenderEnum.MALE,
            phone_number="+919999000002",
        )
        db.add(patient)
        db.flush()

        dept = db.query(Department).first()
        doctor = db.query(User).filter_by(email="admin@apollo.com").first()

        encounter = PatientEncounter(
            patient_id=patient.id,
            encounter_type=EncounterTypeEnum.OPD,
            status=EncounterStatusEnum.ACTIVE,
            start_time=now,
            primary_doctor_id=doctor.id,
            department_id=dept.id,
            chief_complaint_notes="Fever and severe cough for 3 days",
        )
        db.add(encounter)
        db.flush()

        vital = EncounterVital(
            encounter_id=encounter.id,
            temperature_f=101.4,
            pulse_bpm=88,
            bp_systolic=120,
            bp_diastolic=80,
            respiratory_rate=18,
            spo2_percent=98,
            weight_kg=72.5,
            height_cm=175.0,
            bmi=23.7,
            notes="Patient comfortable on room air",
        )
        db.add(vital)
        db.flush()

        diag_master = db.query(DiagnosisMaster).first()
        if not diag_master:
            diag_master = DiagnosisMaster(
                department_id=dept.id,
                icd10_code="J06.9",
                short_description="Acute upper respiratory infection",
                full_description="Acute upper respiratory infection, unspecified",
            )
            db.add(diag_master)
            db.flush()

        condition = EncounterDiagnosis(
            encounter_id=encounter.id,
            diagnosis_id=diag_master.id,
            diagnosis_type=DiagnosisTypeEnum.PRIMARY,
            clinical_remarks="Likely viral infection",
        )
        db.add(condition)
        db.commit()

        patient_id = patient.id
        encounter_id = encounter.id
        vital_id = vital.id
        condition_id = condition.id
        icd_code = diag_master.icd10_code

    headers = apollo_admin_headers

    # --- 1. Test Encounter Read & Search ---
    enc_read = client.get(f"/v1/api/fhir/R4/Encounter/{encounter_id}", headers=headers)
    assert enc_read.status_code == 200, enc_read.text
    enc_data = enc_read.json()
    assert enc_data["resourceType"] == "Encounter"
    assert enc_data["id"] == str(encounter_id)
    assert enc_data["status"] == "in-progress"
    assert enc_data["class"]["code"] == "AMB"
    assert enc_data["subject"]["reference"] == f"Patient/{patient_id}"
    assert enc_data["reasonCode"][0]["text"] == "Fever and severe cough for 3 days"

    enc_search = client.get(
        f"/v1/api/fhir/R4/Encounter?patient=Patient/{patient_id}&status=in-progress",
        headers=headers,
    )
    assert enc_search.status_code == 200
    enc_bundle = enc_search.json()
    assert enc_bundle["resourceType"] == "Bundle"
    assert enc_bundle["total"] >= 1
    assert any(e["resource"]["id"] == str(encounter_id) for e in enc_bundle["entry"])

    enc_not_found = client.get("/v1/api/fhir/R4/Encounter/999999", headers=headers)
    assert enc_not_found.status_code == 404
    assert enc_not_found.json()["resourceType"] == "OperationOutcome"

    # --- 2. Test Observation (Vitals) Read & Search ---
    obs_read = client.get(f"/v1/api/fhir/R4/Observation/{vital_id}", headers=headers)
    assert obs_read.status_code == 200, obs_read.text
    obs_data = obs_read.json()
    assert obs_data["resourceType"] == "Observation"
    assert obs_data["id"] == str(vital_id)
    assert obs_data["status"] == "final"
    assert obs_data["category"][0]["coding"][0]["code"] == "vital-signs"
    assert obs_data["subject"]["reference"] == f"Patient/{patient_id}"
    assert obs_data["encounter"]["reference"] == f"Encounter/{encounter_id}"
    comp_codes = [c["code"]["coding"][0]["code"] for c in obs_data["component"]]
    assert "8310-5" in comp_codes  # temperature
    assert "8867-4" in comp_codes  # pulse
    assert "8480-6" in comp_codes  # systolic bp
    assert "39156-5" in comp_codes  # bmi

    obs_search = client.get(
        f"/v1/api/fhir/R4/Observation?encounter=Encounter/{encounter_id}",
        headers=headers,
    )
    assert obs_search.status_code == 200
    obs_bundle = obs_search.json()
    assert obs_bundle["resourceType"] == "Bundle"
    assert obs_bundle["total"] >= 1
    assert any(e["resource"]["id"] == str(vital_id) for e in obs_bundle["entry"])

    obs_not_found = client.get("/v1/api/fhir/R4/Observation/999999", headers=headers)
    assert obs_not_found.status_code == 404
    assert obs_not_found.json()["resourceType"] == "OperationOutcome"

    # --- 3. Test Condition (Diagnoses) Read & Search ---
    cond_read = client.get(f"/v1/api/fhir/R4/Condition/{condition_id}", headers=headers)
    assert cond_read.status_code == 200, cond_read.text
    cond_data = cond_read.json()
    assert cond_data["resourceType"] == "Condition"
    assert cond_data["id"] == str(condition_id)
    assert cond_data["clinicalStatus"]["coding"][0]["code"] == "active"
    assert cond_data["verificationStatus"]["coding"][0]["code"] == "confirmed"
    assert cond_data["category"][0]["coding"][0]["code"] == "encounter-diagnosis"
    assert cond_data["code"]["coding"][0]["code"] == icd_code
    assert cond_data["subject"]["reference"] == f"Patient/{patient_id}"
    assert cond_data["encounter"]["reference"] == f"Encounter/{encounter_id}"
    assert cond_data["note"][0]["text"] == "Likely viral infection"

    cond_search = client.get(
        f"/v1/api/fhir/R4/Condition?patient=Patient/{patient_id}&code={icd_code}",
        headers=headers,
    )
    assert cond_search.status_code == 200
    cond_bundle = cond_search.json()
    assert cond_bundle["resourceType"] == "Bundle"
    assert cond_bundle["total"] >= 1
    assert any(e["resource"]["id"] == str(condition_id) for e in cond_bundle["entry"])

    cond_not_found = client.get("/v1/api/fhir/R4/Condition/999999", headers=headers)
    assert cond_not_found.status_code == 404
    assert cond_not_found.json()["resourceType"] == "OperationOutcome"
