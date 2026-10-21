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


def test_fhir_clinical_role_matrix_authorization_parity(
    client,
    multi_tenant_env,
    superuser_headers,
    doctor_headers,
    nurse_headers,
    pharmacist_headers,
    billing_headers,
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        patient = Patient(
            mrn="PARITY-001",
            full_name="Parity Test Patient",
            date_of_birth=datetime(1988, 3, 20, tzinfo=timezone.utc),
            gender=GenderEnum.FEMALE,
            phone_number="+919876543000",
            is_active=True,
        )
        db.add(patient)
        db.flush()

        dept = db.query(Department).first()
        doctor = db.query(User).filter_by(email="doctor@default.com").first()

        encounter = PatientEncounter(
            patient_id=patient.id,
            encounter_type=EncounterTypeEnum.OPD,
            status=EncounterStatusEnum.ACTIVE,
            start_time=now,
            primary_doctor_id=doctor.id,
            department_id=dept.id,
            chief_complaint_notes="Routine checkup",
        )
        db.add(encounter)
        db.flush()

        vital = EncounterVital(
            encounter_id=encounter.id,
            temperature_f=98.6,
            pulse_bpm=72,
            bp_systolic=120,
            bp_diastolic=80,
            spo2_percent=99,
        )
        db.add(vital)
        db.flush()

        diag_master = db.query(DiagnosisMaster).first()
        if not diag_master:
            diag_master = DiagnosisMaster(
                department_id=dept.id,
                icd10_code="I10",
                short_description="Essential hypertension",
                full_description="Essential (primary) hypertension",
            )
            db.add(diag_master)
            db.flush()

        condition = EncounterDiagnosis(
            encounter_id=encounter.id,
            diagnosis_id=diag_master.id,
            diagnosis_type=DiagnosisTypeEnum.PRIMARY,
        )
        db.add(condition)
        db.commit()

        p_id = patient.id
        enc_id = encounter.id
        vit_id = vital.id
        cond_id = condition.id

    # 1. Clinical roles (Doctor, Nurse, Admin) MUST have access to FHIR and Internal clinical data
    for name, headers in [("Doctor", doctor_headers), ("Nurse", nurse_headers), ("Admin", superuser_headers)]:
        # Internal clinical endpoints
        assert client.get(f"/v1/api/clinical/encounters/{enc_id}/vitals", headers=headers).status_code == 200, f"{name} failed internal vitals"
        assert client.get(f"/v1/api/clinical/encounters/{enc_id}/diagnoses", headers=headers).status_code == 200, f"{name} failed internal diagnoses"
        assert client.get(f"/v1/api/adt/encounters/{enc_id}", headers=headers).status_code == 200, f"{name} failed internal adt"

        # FHIR endpoints
        assert client.get(f"/v1/api/fhir/R4/Encounter/{enc_id}", headers=headers).status_code == 200, f"{name} failed fhir encounter read"
        assert client.get(f"/v1/api/fhir/R4/Observation/{vit_id}", headers=headers).status_code == 200, f"{name} failed fhir observation read"
        assert client.get(f"/v1/api/fhir/R4/Condition/{cond_id}", headers=headers).status_code == 200, f"{name} failed fhir condition read"
        assert client.get("/v1/api/fhir/R4/Encounter", headers=headers).status_code == 200, f"{name} failed fhir encounter search"
        assert client.get("/v1/api/fhir/R4/Observation", headers=headers).status_code == 200, f"{name} failed fhir observation search"
        assert client.get("/v1/api/fhir/R4/Condition", headers=headers).status_code == 200, f"{name} failed fhir condition search"

    # 2. Non-clinical roles (Pharmacist, Billing) MUST BE FORBIDDEN (403) from FHIR clinical data and Internal clinical data
    for name, headers in [("Pharmacist", pharmacist_headers), ("Billing", billing_headers)]:
        # Internal clinical endpoints return 403
        assert client.get(f"/v1/api/clinical/encounters/{enc_id}/vitals", headers=headers).status_code == 403, f"{name} unexpectedly had internal vitals access"
        assert client.get(f"/v1/api/clinical/encounters/{enc_id}/diagnoses", headers=headers).status_code == 403, f"{name} unexpectedly had internal diagnoses access"
        assert client.get(f"/v1/api/adt/encounters/{enc_id}", headers=headers).status_code == 403, f"{name} unexpectedly had internal adt access"

        # FHIR clinical reads MUST ALSO return 403 (fixing F-01)
        assert client.get(f"/v1/api/fhir/R4/Encounter/{enc_id}", headers=headers).status_code == 403, f"{name} unexpectedly had fhir encounter read access"
        assert client.get(f"/v1/api/fhir/R4/Observation/{vit_id}", headers=headers).status_code == 403, f"{name} unexpectedly had fhir observation read access"
        assert client.get(f"/v1/api/fhir/R4/Condition/{cond_id}", headers=headers).status_code == 403, f"{name} unexpectedly had fhir condition read access"

        # FHIR clinical searches MUST ALSO return 403 (fixing F-01)
        assert client.get("/v1/api/fhir/R4/Encounter", headers=headers).status_code == 403, f"{name} unexpectedly had fhir encounter search access"
        assert client.get("/v1/api/fhir/R4/Observation", headers=headers).status_code == 403, f"{name} unexpectedly had fhir observation search access"
        assert client.get("/v1/api/fhir/R4/Condition", headers=headers).status_code == 403, f"{name} unexpectedly had fhir condition search access"

        # But they legitimately hold patients:read, so FHIR Patient & internal Patient are allowed (200)
        assert client.get(f"/v1/api/fhir/R4/Patient/{p_id}", headers=headers).status_code == 200, f"{name} was denied fhir patient read"
        assert client.get(f"/v1/api/patients/{p_id}", headers=headers).status_code == 200, f"{name} was denied internal patient read"


def test_fhir_searches_filter_denied_patients(
    client, multi_tenant_env, superuser_headers
):
    from backend.domains.privacy.models import PatientConsentRecord, PrivacyPolicyRevision
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        # Create Patient A (will be denied by consent)
        patient_a = Patient(
            mrn="FHIR-FILTER-A",
            full_name="Patient Denied FHIR",
            date_of_birth=datetime(1991, 2, 2, tzinfo=timezone.utc),
            gender=GenderEnum.FEMALE,
            phone_number="+919876543111",
            is_active=True,
        )
        # Create Patient B (will be allowed)
        patient_b = Patient(
            mrn="FHIR-FILTER-B",
            full_name="Patient Allowed FHIR",
            date_of_birth=datetime(1992, 3, 3, tzinfo=timezone.utc),
            gender=GenderEnum.MALE,
            phone_number="+919876543222",
            is_active=True,
        )
        db.add_all([patient_a, patient_b])
        db.flush()

        dept = db.query(Department).first()
        doctor = db.query(User).filter_by(email="doctor@default.com").first()

        enc_a = PatientEncounter(
            patient_id=patient_a.id,
            encounter_type=EncounterTypeEnum.OPD,
            status=EncounterStatusEnum.ACTIVE,
            start_time=now,
            primary_doctor_id=doctor.id,
            department_id=dept.id,
            chief_complaint_notes="Complaint A",
        )
        enc_b = PatientEncounter(
            patient_id=patient_b.id,
            encounter_type=EncounterTypeEnum.OPD,
            status=EncounterStatusEnum.ACTIVE,
            start_time=now,
            primary_doctor_id=doctor.id,
            department_id=dept.id,
            chief_complaint_notes="Complaint B",
        )
        db.add_all([enc_a, enc_b])
        db.flush()

        vit_a = EncounterVital(encounter_id=enc_a.id, temperature_f=99.0, pulse_bpm=75)
        vit_b = EncounterVital(encounter_id=enc_b.id, temperature_f=98.4, pulse_bpm=70)
        db.add_all([vit_a, vit_b])
        db.flush()

        diag_master = db.query(DiagnosisMaster).first()
        cond_a = EncounterDiagnosis(encounter_id=enc_a.id, diagnosis_id=diag_master.id, diagnosis_type=DiagnosisTypeEnum.PRIMARY)
        cond_b = EncounterDiagnosis(encounter_id=enc_b.id, diagnosis_id=diag_master.id, diagnosis_type=DiagnosisTypeEnum.PRIMARY)
        db.add_all([cond_a, cond_b])
        db.flush()

        p_a_id = patient_a.id
        p_b_id = patient_b.id
        enc_a_id = enc_a.id
        enc_b_id = enc_b.id
        vit_a_id = vit_a.id
        vit_b_id = vit_b.id
        cond_a_id = cond_a.id
        cond_b_id = cond_b.id

        policy_doc = {
            "schema_version": 1,
            "policy_code": "fhir-filter-policy",
            "purposes": [
                {
                    "code": "FHIR_SEARCH_PURPOSE",
                    "display_name": "FHIR Search",
                    "consent_required": True,
                    "legal_basis": "Testing FHIR row filtering.",
                    "required_before": ["disclose"],
                    "scope": "patient",
                    "validity": "until_withdrawn",
                    "capture": ["WRITTEN"],
                    "consenters": ["patient"],
                    "default_when_no_record": "allow",
                }
            ],
            "data_use_points": {
                "fhir.patient.search": "FHIR_SEARCH_PURPOSE",
                "fhir.encounter.search": "FHIR_SEARCH_PURPOSE",
                "fhir.observation.search": "FHIR_SEARCH_PURPOSE",
                "fhir.condition.search": "FHIR_SEARCH_PURPOSE",
            },
            "exceptions": [],
            "never_override": [],
            "groups": [],
            "withdrawal": {
                "effective": "immediate",
                "cancel_queued": True,
                "cancel_in_flight": True,
                "notify_prior_recipients": "never",
                "statutory_retention_continues": True,
                "patient_notice": "Notice text.",
                "clinician_alert_purposes": [],
                "does_not_affect_treatment": True,
            },
        }
        res = client.post(
            "/v1/api/privacy/policies",
            headers=superuser_headers,
            json={"policy_type": "CONSENT", "policy_document": policy_doc},
        )
        assert res.status_code == 201
        rev_id = res.json()["id"]

        rev = db.get(PrivacyPolicyRevision, rev_id)
        rev.configured_by_user_id = doctor.id
        db.commit()

        appr = client.post(
            f"/v1/api/privacy/policies/{rev_id}/approve",
            headers=superuser_headers,
            json={"approval_reference": "FHIR-FILTER-APPROVE"},
        )
        assert appr.status_code == 200

        rev = db.get(PrivacyPolicyRevision, rev_id)
        rev.status = 'APPROVED'
        rev.mode_set_by_user_id = doctor.id
        rev.mode_set_at = datetime.now(timezone.utc)
        rev.mode_approval_reference = 'FHIR-APP-REF'
        rev.is_active = True
        rev.enforcement_mode = "ENFORCE"

        # Record DECLINED consent for patient A
        db.add(
            PatientConsentRecord(
                policy_revision_id=rev_id,
                patient_id=p_a_id,
                recorded_by_user_id=doctor.id,
                consent_type="CARE",
                purpose="FHIR_SEARCH_PURPOSE",
                decision="DECLINED",
                source="WRITTEN",
                recorded_at=datetime.now(timezone.utc),
                scope_type="patient",
                consenter_type="patient",
            )
        )
        db.commit()

    # 1. FHIR Patient search: Patient A must NOT appear; Patient B MUST appear
    pat_search = client.get(
        "/v1/api/fhir/R4/Patient",
        params={"name": "FHIR"},
        headers=superuser_headers,
    )
    assert pat_search.status_code == 200
    pat_bundle = pat_search.json()
    pat_entry_ids = [e["resource"]["id"] for e in pat_bundle.get("entry", [])]
    assert str(p_a_id) not in pat_entry_ids
    assert str(p_b_id) in pat_entry_ids
    assert pat_bundle["total"] == 1

    # 2. FHIR Encounter search: Patient A's encounter must NOT appear; Patient B's encounter MUST appear
    enc_search = client.get(
        "/v1/api/fhir/R4/Encounter",
        headers=superuser_headers,
    )
    assert enc_search.status_code == 200
    enc_bundle = enc_search.json()
    enc_entry_ids = [e["resource"]["id"] for e in enc_bundle.get("entry", [])]
    assert str(enc_a_id) not in enc_entry_ids
    assert str(enc_b_id) in enc_entry_ids

    # 3. FHIR Observation search: Patient A's observation must NOT appear; Patient B's observation MUST appear
    obs_search = client.get(
        "/v1/api/fhir/R4/Observation",
        headers=superuser_headers,
    )
    assert obs_search.status_code == 200
    obs_bundle = obs_search.json()
    obs_entry_ids = [e["resource"]["id"] for e in obs_bundle.get("entry", [])]
    assert str(vit_a_id) not in obs_entry_ids
    assert str(vit_b_id) in obs_entry_ids

    # 4. FHIR Condition search: Patient A's condition must NOT appear; Patient B's condition MUST appear
    cond_search = client.get(
        "/v1/api/fhir/R4/Condition",
        headers=superuser_headers,
    )
    assert cond_search.status_code == 200
    cond_bundle = cond_search.json()
    cond_entry_ids = [e["resource"]["id"] for e in cond_bundle.get("entry", [])]
    assert str(cond_a_id) not in cond_entry_ids
    assert str(cond_b_id) in cond_entry_ids
