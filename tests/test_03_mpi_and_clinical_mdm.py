from backend.control_plane.models import GlobalPatientPointer


def test_01_register_patient_generates_mrn_and_syncs_discovery_pointer(client, multi_tenant_env, doctor_headers):
    """Scenario 1: Registering a patient auto-generates MRN and writes a GlobalPatientPointer in Control Plane."""
    res = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Ramesh Kumar",
            "date_of_birth": "1982-07-20T00:00:00Z",
            "gender": "MALE",
            "phone_number": "9988776655",
            "abha_number": "ABHA-100001",
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["mrn"].startswith("MRN-")
    assert data["full_name"] == "Ramesh Kumar"

    with multi_tenant_env["control_session_factory"]() as cdb:
        ptr = cdb.query(GlobalPatientPointer).filter(GlobalPatientPointer.abha_number == "ABHA-100001").first()
        assert ptr is not None
        assert ptr.branch_code == "HQ-HYD-01"
        assert ptr.source_mrn == data["mrn"]


def test_02_duplicate_abha_or_mrn_returns_409_conflict(client, doctor_headers):
    """Scenario 2: Duplicate ABHA number or duplicate MRN is rejected with 409 Conflict."""
    payload = {
        "mrn": "MRN-CUSTOM-01",
        "full_name": "Priya Sharma",
        "date_of_birth": "1992-03-10T00:00:00Z",
        "gender": "FEMALE",
        "phone_number": "9123456780",
        "abha_number": "ABHA-DUP-999",
    }
    res1 = client.post("/v1/api/patients", headers=doctor_headers, json=payload)
    assert res1.status_code == 201

    # Duplicate ABHA
    dup_abha = {**payload, "mrn": "MRN-CUSTOM-02"}
    assert client.post("/v1/api/patients", headers=doctor_headers, json=dup_abha).status_code == 409

    # Duplicate MRN
    dup_mrn = {**payload, "abha_number": "ABHA-UNIQUE-888"}
    assert client.post("/v1/api/patients", headers=doctor_headers, json=dup_mrn).status_code == 409


def test_03_search_patients_by_name_mrn_phone_and_abha(client, doctor_headers):
    """Scenario 3: GET /api/patients?search=... filters across full_name, mrn, phone_number, and abha_number."""
    p = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Ananya Verma",
            "date_of_birth": "1995-11-05T00:00:00Z",
            "gender": "FEMALE",
            "phone_number": "9876501234",
            "abha_number": "ABHA-SEARCH-77",
        },
    ).json()

    for term in ("Ananya", p["mrn"], "9876501234", "ABHA-SEARCH-77"):
        search_res = client.get(f"/v1/api/patients?search={term}", headers=doctor_headers)
        assert search_res.status_code == 200
        assert search_res.json()["total"] == 1
        assert search_res.json()["items"][0]["id"] == p["id"]


def test_04_chart_patient_allergy_and_idempotent_update(client, multi_tenant_env, doctor_headers):
    """Scenario 4: Charting an allergy records it and re-charting the same allergy updates severity/reaction."""
    p = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Karthik Nair",
            "date_of_birth": "1980-01-15T00:00:00Z",
            "gender": "MALE",
            "phone_number": "9000011111",
            "abha_number": "ABHA-ALG-01",
        },
    ).json()

    # First chart: MILD
    c1 = client.post(
        f"/v1/api/patients/{p['id']}/allergies",
        headers=doctor_headers,
        json={"allergy_id": 1, "severity": "MILD", "reaction": "Mild skin itch"},
    )
    assert c1.status_code == 201
    assert c1.json()["severity"] == "MILD"

    # Re-chart same allergy_id=1 as ANAPHYLACTIC -> updates existing record idempotently
    c2 = client.post(
        f"/v1/api/patients/{p['id']}/allergies",
        headers=doctor_headers,
        json={"allergy_id": 1, "severity": "ANAPHYLACTIC", "reaction": "Severe airway constriction"},
    )
    assert c2.status_code == 201
    assert c2.json()["id"] == c1.json()["id"]
    assert c2.json()["severity"] == "ANAPHYLACTIC"

    # Verify Central Discovery Index pointer is now marked has_critical_allergies=True
    with multi_tenant_env["control_session_factory"]() as cdb:
        ptr = cdb.query(GlobalPatientPointer).filter(GlobalPatientPointer.abha_number == "ABHA-ALG-01").first()
        assert ptr.has_critical_allergies is True


def test_05_deactivate_patient_hides_from_active_list(client, superuser_headers):
    """Scenario 5: DELETE /api/patients/{id} soft-deactivates patient and excludes them from active listings."""
    p = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={
            "full_name": "Deactivate Target",
            "date_of_birth": "1975-05-05T00:00:00Z",
            "gender": "OTHER",
            "phone_number": "9111122222",
        },
    ).json()

    del_res = client.delete(f"/v1/api/patients/{p['id']}", headers=superuser_headers)
    assert del_res.status_code == 200

    assert client.get(f"/v1/api/patients/{p['id']}", headers=superuser_headers).status_code == 404
    assert client.get("/v1/api/patients?search=Deactivate", headers=superuser_headers).json()["total"] == 0


def test_06_clinical_mdm_crud_all_five_dictionaries(client, superuser_headers):
    """Scenario 6: Create, update, and deactivate items across all 5 clinical MDM dictionaries."""
    # 1. Allergy
    alg = client.post(
        "/v1/api/clinical/allergies",
        headers=superuser_headers,
        json={"department_id": 1, "code": "ALG_SHELL", "name": "Shellfish", "category": "FOOD", "default_severity": "SEVERE"},
    ).json()
    assert client.put(f"/v1/api/clinical/allergies/{alg['id']}", headers=superuser_headers, json={"name": "Crustacean Shellfish"}).status_code == 200
    assert client.delete(f"/v1/api/clinical/allergies/{alg['id']}", headers=superuser_headers).status_code == 200

    # 2. Chief Complaint
    cc = client.post(
        "/v1/api/clinical/complaints",
        headers=superuser_headers,
        json={"department_id": 1, "code": "CC_VERTIGO", "name": "Acute Vertigo"},
    ).json()
    assert client.put(f"/v1/api/clinical/complaints/{cc['id']}", headers=superuser_headers, json={"name": "Severe Dizziness / Vertigo"}).status_code == 200
    assert client.delete(f"/v1/api/clinical/complaints/{cc['id']}", headers=superuser_headers).status_code == 200

    # 3. Comorbidity
    cm = client.post(
        "/v1/api/clinical/comorbidities",
        headers=superuser_headers,
        json={"department_id": 1, "code": "CM_HYPO", "name": "Hypothyroidism", "icd10_code": "E03.9"},
    ).json()
    assert client.put(f"/v1/api/clinical/comorbidities/{cm['id']}", headers=superuser_headers, json={"name": "Primary Hypothyroidism"}).status_code == 200
    assert client.delete(f"/v1/api/clinical/comorbidities/{cm['id']}", headers=superuser_headers).status_code == 200

    # 4. ICD-10 Diagnosis
    dx = client.post(
        "/v1/api/clinical/diagnoses",
        headers=superuser_headers,
        json={"department_id": 1, "icd10_code": "G43.9", "short_description": "Migraine", "full_description": "Migraine, unspecified"},
    ).json()
    assert client.put(f"/v1/api/clinical/diagnoses/{dx['id']}", headers=superuser_headers, json={"short_description": "Chronic Migraine"}).status_code == 200
    assert client.delete(f"/v1/api/clinical/diagnoses/{dx['id']}", headers=superuser_headers).status_code == 200

    # 5. Physical Examination
    pe = client.post(
        "/v1/api/clinical/physical-exams",
        headers=superuser_headers,
        json={"department_id": 1, "body_system": "CARDIOVASCULAR", "code": "PE_S3", "name": "S3 Gallop"},
    ).json()
    assert client.put(f"/v1/api/clinical/physical-exams/{pe['id']}", headers=superuser_headers, json={"name": "Ventricular S3 Gallop"}).status_code == 200
    assert client.delete(f"/v1/api/clinical/physical-exams/{pe['id']}", headers=superuser_headers).status_code == 200


def test_07_tenant_namespaced_redis_taxonomy_caching(client, fake_redis, superuser_headers, apollo_admin_headers):
    """Scenario 7: Taxonomy cache keys are strictly namespaced per tenant (<slug>:taxonomy:<type>:<dept_id>)."""
    res_def = client.get("/v1/api/clinical/departments/1/taxonomy/allergies", headers=superuser_headers)
    assert res_def.status_code == 200
    assert "default:taxonomy:allergies:1" in fake_redis.store
    assert "apollo:taxonomy:allergies:1" not in fake_redis.store

    res_apo = client.get("/v1/api/clinical/departments/1/taxonomy/allergies", headers=apollo_admin_headers)
    assert res_apo.status_code == 200
    assert "apollo:taxonomy:allergies:1" in fake_redis.store


def test_08_taxonomy_cache_invalidation_on_mutation(client, fake_redis, superuser_headers, apollo_admin_headers):
    """Scenario 8: Mutating a dictionary item in 'default' invalidates 'default' cache without touching 'apollo' cache."""
    client.get("/v1/api/clinical/departments/1/taxonomy/complaints", headers=superuser_headers)
    client.get("/v1/api/clinical/departments/1/taxonomy/complaints", headers=apollo_admin_headers)
    assert "default:taxonomy:complaints:1" in fake_redis.store
    assert "apollo:taxonomy:complaints:1" in fake_redis.store

    # Create a new complaint in 'default'
    client.post(
        "/v1/api/clinical/complaints",
        headers=superuser_headers,
        json={"department_id": 1, "code": "CC_SYNC", "name": "Syncope"},
    )
    assert "default:taxonomy:complaints:1" not in fake_redis.store
    assert "apollo:taxonomy:complaints:1" in fake_redis.store


def test_09_cpoe_allergy_hard_stop_direct_and_cross_reactive(client, doctor_headers):
    """Scenario 9: Severe Penicillin allergy blocks direct Penicillin AND cross-reactive Amoxicillin/Augmentin (409)."""
    p = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={"full_name": "Allergy HardStop Patient", "date_of_birth": "1985-06-15T00:00:00Z", "gender": "MALE"},
    ).json()

    # Chart ANAPHYLACTIC allergy to ID 1 (Penicillin / Beta-Lactams)
    client.post(
        f"/v1/api/patients/{p['id']}/allergies",
        headers=doctor_headers,
        json={"allergy_id": 1, "severity": "ANAPHYLACTIC", "reaction": "Anaphylaxis"},
    )

    # Direct match blocked (409)
    res_direct = client.post(
        "/v1/api/clinical/cpoe/allergy-check",
        headers=doctor_headers,
        json={"patient_id": p["id"], "allergen_keywords": ["Penicillin G"]},
    )
    assert res_direct.status_code == 409
    assert "CLINICAL HARD-STOP" in res_direct.json()["message"]

    # Cross-reactive beta-lactam (Amoxicillin / Augmentin) blocked (409)
    res_cross = client.post(
        "/v1/api/clinical/cpoe/allergy-check",
        headers=doctor_headers,
        json={"patient_id": p["id"], "allergen_keywords": ["Augmentin 625mg"]},
    )
    assert res_cross.status_code == 409


def test_10_cpoe_mild_allergy_does_not_trigger_hard_stop(client, doctor_headers):
    """Scenario 10: MILD or MODERATE allergies return 200 SAFE on hard-stop check."""
    p = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={"full_name": "Mild Allergy Patient", "date_of_birth": "1990-02-20T00:00:00Z", "gender": "FEMALE"},
    ).json()

    client.post(
        f"/v1/api/patients/{p['id']}/allergies",
        headers=doctor_headers,
        json={"allergy_id": 5, "severity": "MILD", "reaction": "Minor contact rash"},
    )

    res = client.post(
        "/v1/api/clinical/cpoe/allergy-check",
        headers=doctor_headers,
        json={"patient_id": p["id"], "allergen_keywords": ["Latex"]},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "SAFE"