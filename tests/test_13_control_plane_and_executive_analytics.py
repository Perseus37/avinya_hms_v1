from fastapi.testclient import TestClient
from config import settings
from database import get_control_db
from onboarding_portal import onboarding_app


def _get_onboarding_client(client: TestClient) -> TestClient:
    """Wires onboarding_app to use the same test Control Plane DB override as the main test suite."""
    if get_control_db in client.app.dependency_overrides:
        onboarding_app.dependency_overrides[get_control_db] = client.app.dependency_overrides[get_control_db]
    return TestClient(onboarding_app)


def test_01_control_plane_list_tenants_and_operational_telemetry(client):
    """Scenario 1: Control Plane /api/tenants lists registered hospitals and /api/tenants/default/telemetry returns live Data Plane counts."""
    cp_client = _get_onboarding_client(client)

    assert cp_client.get("/api/tenants", headers={"X-Master-Setup-Key": "wrong_key"}).status_code == 403

    res = cp_client.get("/api/tenants", headers={"X-Master-Setup-Key": settings.MASTER_ONBOARDING_KEY})
    assert res.status_code == 200
    slugs = [t["subdomain_slug"] for t in res.json()["items"]]
    assert "default" in slugs
    assert "apollo" in slugs

    tel_res = cp_client.get("/api/tenants/default/telemetry", headers={"X-Master-Setup-Key": settings.MASTER_ONBOARDING_KEY})
    assert tel_res.status_code == 200
    metrics = tel_res.json()["metrics"]
    assert metrics["users_count"] >= 5
    assert "gross_revenue_paise" in metrics


def test_02_control_plane_circuit_breaker_key_rotation_admin_reset_and_fleet_sync(client):
    """Scenario 2: Control Plane suspends/reactivates a tenant, rotates keys, resets locked admin password, and syncs fleet templates."""
    cp_client = _get_onboarding_client(client)
    master_key = settings.MASTER_ONBOARDING_KEY

    susp_res = cp_client.patch(
        "/api/tenants/apollo/status",
        json={"master_setup_key": master_key, "is_active": False},
    )
    assert susp_res.status_code == 200
    assert susp_res.json()["is_active"] is False

    act_res = cp_client.patch(
        "/api/tenants/apollo/status",
        json={"master_setup_key": master_key, "is_active": True},
    )
    assert act_res.status_code == 200
    assert act_res.json()["is_active"] is True

    rot_res = cp_client.post(
        "/api/tenants/apollo/rotate-keys",
        json={
            "master_setup_key": master_key,
            "rotate_jwt_secret": True,
            "rotate_backup_key": True,
            "rotate_federation_key": True,
        },
    )
    assert rot_res.status_code == 200
    assert "new_jwt_secret_key" in rot_res.json()
    assert rot_res.json()["new_federation_api_key"].startswith("fed_live_")

    pwd_res = cp_client.post(
        "/api/tenants/apollo/reset-admin-password",
        json={"master_setup_key": master_key},
    )
    assert pwd_res.status_code == 200
    new_pwd = pwd_res.json()["one_time_password"]
    admin_email = pwd_res.json()["admin_email"]

    login_res = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "apollo"},
        json={"email": admin_email, "password": new_pwd},
    )
    assert login_res.status_code == 200

    sync_res = cp_client.post(
        "/api/tenants/sync-fleet",
        json={"master_setup_key": master_key},
    )
    assert sync_res.status_code == 200
    assert sync_res.json()["synced_tenants_count"] >= 3

    # Verify On-Premise RAM Bootstrap & Canonical Template Sync Manifest
    manifest_res = cp_client.get(
        "/api/onprem/sync-manifest",
        headers={"X-Federation-API-Key": rot_res.json()["new_federation_api_key"]},
    )
    assert manifest_res.status_code == 200
    manifest_data = manifest_res.json()
    assert manifest_data["canonical_system_templates_count"] >= 10
    assert "jwt_secret_key" in manifest_data["in_memory_secrets"]


def test_03_executive_command_center_bed_occupancy_alos_readmissions_and_arpob(
    client, superuser_headers
):
    """Scenario 3: GET /v1/api/analytics/executive-command-center computes Spatial Bed Census, Room/Bed Types, ALOS, Readmission Rate, and ARPOB."""
    res = client.get("/v1/api/analytics/executive-command-center", headers=superuser_headers)
    assert res.status_code == 200
    data = res.json()

    assert "by_ward_type" in data["bed_census"]
    assert "by_room_type" in data["bed_census"]
    assert "by_bed_type" in data["bed_census"]
    assert "alos_hours_by_admission_type" in data["encounters_and_ipd_alos"]
    assert "readmission_rate_pct" in data["encounters_and_ipd_alos"]
    assert "arpob_paise" in data["unit_economics"]


def test_04_financial_revenue_cycle_ar_aging_mou_savings_and_tpa_pipeline(
    client, billing_headers
):
    """Scenario 4: GET /v1/api/analytics/financial-revenue-cycle returns A/R aging buckets, 8 clinical streams, MoU savings index, and TPA pipeline."""
    res = client.get("/v1/api/analytics/financial-revenue-cycle", headers=billing_headers)
    assert res.status_code == 200
    data = res.json()

    wf = data["revenue_waterfall"]
    assert "ar_aging_buckets_paise" in wf
    assert "0_to_30_days_paise" in wf["ar_aging_buckets_paise"]
    assert "top_revenue_generating_services" in data
    assert "claim_settlement_ratio_pct" in data["insurance_and_tpa_pipeline"]
    assert "deposits_and_refunds_ledger" in data


def test_05_patient_demographics_abha_adoption_and_news2_early_warning_radar(
    client, doctor_headers
):
    """Scenario 5: Verify MPI Age-Cohort Pyramid, ABHA Adoption %, Allergy Stratification, NEWS2 Vitals Early-Warning triggers, and Fluid Radar."""
    p = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "High Risk Vitals Patient",
            "date_of_birth": "1955-05-10T00:00:00Z",
            "gender": "MALE",
            "abha_number": "91-8877-6655-4433",
            "blood_group": "O+",
        },
    ).json()
    enc = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={"patient_id": p["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1},
    ).json()

    # Record abnormal vitals (SpO2 89%, BP 185/110, Temp 103.1F) -> Triggers NEWS2 early warning counters!
    client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/vitals",
        headers=doctor_headers,
        json={"spo2_percent": 89, "bp_systolic": 185, "bp_diastolic": 110, "temperature_f": 103.1, "weight_kg": 95.0, "height_cm": 170.0},
    )

    demo_res = client.get("/v1/api/analytics/patient-demographics-and-epidemiology", headers=doctor_headers)
    assert demo_res.status_code == 200
    assert demo_res.json()["mpi_population_summary"]["abha_linked_patients_count"] >= 1
    assert demo_res.json()["mpi_population_summary"]["age_cohort_pyramid"]["60_plus_senior_citizen"] >= 1

    ew_res = client.get("/v1/api/analytics/clinical-vitals-and-early-warning", headers=doctor_headers)
    assert ew_res.status_code == 200
    triggers = ew_res.json()["news2_early_warning_triggers"]
    assert triggers["hypoxia_spo2_lt_92_count"] >= 1
    assert triggers["hypertensive_crisis_sys_ge_180_count"] >= 1
    assert triggers["high_pyrexia_temp_ge_102f_count"] >= 1
    assert ew_res.json()["bmi_nutritional_distribution"]["obese_ge_30"] >= 1


def test_06_clinical_quality_pharmacy_fefo_doctor_productivity_and_inventory_radar(
    client, superuser_headers, doctor_headers
):
    """Scenario 6: Verify NABH/JCI Clinical Quality, Pharmacy Dispensing Fill Rate & FEFO Valuation, Doctor Productivity, and Blood/Security Radar."""
    assert client.get("/v1/api/analytics/clinical-quality-and-safety", headers=doctor_headers).status_code == 200

    pharm_res = client.get("/v1/api/analytics/pharmacy-and-therapeutics", headers=superuser_headers)
    assert pharm_res.status_code == 200
    assert "dispensing_fill_rate_pct" in pharm_res.json()["prescription_funnel"]
    assert "fefo_inventory_risk_valuation_paise" in pharm_res.json()

    prod_res = client.get("/v1/api/analytics/doctor-and-department-productivity", headers=superuser_headers)
    assert prod_res.status_code == 200
    assert "estimated_consultation_revenue_paise" in prod_res.json()["doctors"][0]

    inv_res = client.get("/v1/api/analytics/inventory-and-blood-radar", headers=superuser_headers)
    assert inv_res.status_code == 200
    assert "total_active_report_templates" in inv_res.json()["governance_and_security"]