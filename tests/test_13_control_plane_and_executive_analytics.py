from fastapi.testclient import TestClient
from backend.core.config import settings
from backend.core.database import get_control_db
from backend.control_plane.app import app as control_plane_app
from backend.control_plane.models import TenantRegistry
from backend.control_plane.services.pricing import (
    monthly_fee_from_annual_paise,
    normalize_contract_arr_paise,
)


def _get_onboarding_client(client: TestClient) -> TestClient:
    """Wires the control-plane app to the main test suite's control DB override."""
    if get_control_db in client.app.dependency_overrides:
        control_plane_app.dependency_overrides[get_control_db] = client.app.dependency_overrides[get_control_db]
    return TestClient(
        control_plane_app,
        headers={"X-Control-Plane-Secret": settings.CONTROL_PLANE_MASTER_KEY},
    )


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
    assert data["window"]["timezone"] == "UTC"
    assert "not standard ARPOB" in data["data_notes"][-1]
    assert res.headers["cache-control"] == "private, no-store"


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


def test_fleet_arr_matches_contract_cycles_and_excludes_one_time_fees(client, multi_tenant_env):
    session_factory = multi_tenant_env["control_session_factory"]
    with session_factory() as db:
        tenants = {
            tenant.subdomain_slug: tenant
            for tenant in db.query(TenantRegistry).all()
        }
        tenants["default"].billing_cycle_string = "ANNUAL_LICENSE"
        tenants["default"].base_license_fee_paise = 100_000
        tenants["default"].enabled_modules_json = '{"ipd_adt": true}'
        tenants["apollo"].billing_cycle_string = "MONTHLY_SUBSCRIPTION"
        tenants["apollo"].base_license_fee_paise = 10_050
        tenants["apollo"].enabled_modules_json = "{}"
        tenants["sunrise"].billing_cycle_string = "ONE_TIME_PERPETUAL"
        tenants["sunrise"].base_license_fee_paise = 300_000
        tenants["sunrise"].enabled_modules_json = '{"ipd_adt": true}'
        tenants["onprem"].billing_cycle_string = "PER_ACTIVE_BED_MONTHLY"
        tenants["onprem"].base_license_fee_paise = 300_000
        tenants["onprem"].enabled_modules_json = '{"ipd_adt": true}'
        tenants["suspended"].base_license_fee_paise = 900_000
        tenants["suspended"].is_active = False
        db.commit()

    cp_client = _get_onboarding_client(client)
    headers = {"X-Master-Setup-Key": settings.MASTER_ONBOARDING_KEY}
    overview = cp_client.get("/api/fleet/overview", headers=headers)
    analytics = cp_client.get("/api/fleet/analytics", headers=headers)
    assert overview.status_code == analytics.status_code == 200

    overview_data = overview.json()
    analytics_data = analytics.json()
    expected_arr = 100_000 + (10_050 * 12) + (300_000 * 12)
    assert overview_data["vendor_annual_license_arr_paise"] == expected_arr
    assert analytics_data["annual_recurring_revenue_paise"] == expected_arr
    assert sum(analytics_data["arr_by_pricing_model_paise"].values()) == expected_arr
    assert analytics_data["one_time_license_value_paise"] == 300_000
    assert sum(
        tenant["normalized_arr_paise"]
        for tenant in analytics_data["hospital_leaderboard"]
        if tenant["is_active"]
    ) == expected_arr
    assert analytics_data["hospital_leaderboard"][
        next(
            index
            for index, tenant in enumerate(analytics_data["hospital_leaderboard"])
            if tenant["subdomain_slug"] == "suspended"
        )
    ]["normalized_arr_paise"] == 0
    assert analytics_data["module_adoption_counts"]["ipd_adt"] == 3


def test_pricing_uses_consistent_half_up_monthly_rounding_and_arr_cycles():
    assert monthly_fee_from_annual_paise(60_600) == 5_100
    assert normalize_contract_arr_paise(10_050, "MONTHLY_SUBSCRIPTION") == 120_600
    assert normalize_contract_arr_paise(300_000, "ONE_TIME_PERPETUAL") == 0


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
    assert demo_res.json()["window"]["timezone"] == "UTC"
    assert sum(demo_res.json()["mpi_population_summary"]["age_cohort_pyramid"].values()) + demo_res.json()["mpi_population_summary"]["future_birth_dates_excluded_from_age_cohorts"] == demo_res.json()["mpi_population_summary"]["total_active_patients"]
    assert demo_res.headers["cache-control"] == "private, no-store"

    ew_res = client.get("/v1/api/analytics/clinical-vitals-and-early-warning", headers=doctor_headers)
    assert ew_res.status_code == 200
    triggers = ew_res.json()["news2_early_warning_triggers"]
    assert triggers["hypoxia_spo2_lt_92_count"] >= 1
    assert triggers["hypertensive_crisis_sys_ge_180_count"] >= 1
    assert triggers["high_pyrexia_temp_ge_102f_count"] >= 1
    assert ew_res.json()["bmi_nutritional_distribution"]["obese_ge_30"] >= 1
    fluid_radar = ew_res.json()["inpatient_fluid_balance_radar"]
    assert "fluid_overload_ge_2000ml_encounters" not in fluid_radar
    assert "net_intake_ge_2000ml_encounters_count" in fluid_radar
    assert "not a validated NEWS2 score" in ew_res.json()["data_notes"][0]
    assert ew_res.headers["cache-control"] == "private, no-store"


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