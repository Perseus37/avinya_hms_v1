import io
import json
from fastapi.testclient import TestClient
from backend.core.config import settings
from backend.core.database import get_control_db
from backend.control_plane.app import app as control_plane_app


def _get_cp_client(client: TestClient) -> TestClient:
    if get_control_db in client.app.dependency_overrides:
        control_plane_app.dependency_overrides[get_control_db] = client.app.dependency_overrides[get_control_db]
    return TestClient(control_plane_app)


def test_01_control_plane_blueprints_pricing_catalog_and_fleet_overview(client):
    """Scenario 1: Verify /api/blueprints, /api/pricing/catalog (12 modules + 4 bundles), and /api/fleet/overview."""
    cp = _get_cp_client(client)
    bp_res = cp.get("/api/blueprints")
    assert bp_res.status_code == 200
    keys = {b["key"] for b in bp_res.json()["blueprints"]}
    assert "TIER_1_QUATERNARY_500_BED" in keys
    assert "BOUTIQUE_MATERNITY_NICU" in keys

    prc_res = cp.get("/api/pricing/catalog")
    assert prc_res.status_code == 200
    assert len(prc_res.json()["modules"]) == 12
    bundle_codes = {b["code"] for b in prc_res.json()["bundles"]}
    assert "BUNDLE_SURGICAL_SUITE" in bundle_codes
    assert "BUNDLE_TOTAL_HOSPITAL" in bundle_codes

    ov_res = cp.get("/api/fleet/overview", headers={"X-Master-Setup-Key": settings.MASTER_ONBOARDING_KEY})
    assert ov_res.status_code == 200
    assert ov_res.json()["active_hospitals"] >= 3
    assert "vendor_annual_license_arr_paise" in ov_res.json()


def test_02_control_plane_modular_entitlements_and_schema_status(client):
    """Scenario 2: Update tenant modular bundle & à la carte fee and inspect fleet schema status."""
    cp = _get_cp_client(client)
    ent_res = cp.patch(
        "/api/tenants/apollo/entitlements",
        json={
            "master_setup_key": settings.MASTER_ONBOARDING_KEY,
            "pricing_model": "BUNDLE_SURGICAL_SUITE",
            "billing_cycle": "ANNUAL_LICENSE",
            "enabled_modules": {
                "ipd_adt": True,
                "ot_surgery": True,
                "pharmacy": True,
                "billing": True,
                "insurance_tpa": True,
                "blood_bank": False,
            },
            "quota_limits": {"max_beds": 300, "max_users": 120},
            "maintenance_mode": False,
        },
    )
    assert ent_res.status_code == 200
    assert ent_res.json()["pricing_model"] == "BUNDLE_SURGICAL_SUITE"
    assert ent_res.json()["base_license_fee_paise"] == 17500000

    sch_res = cp.get("/api/fleet/schema-status", headers={"X-Master-Setup-Key": settings.MASTER_ONBOARDING_KEY})
    assert sch_res.status_code == 200
    assert sch_res.json()["fleet_size"] >= 3


def test_03_tenant_admin_bulk_spatial_matrix_generator(client, superuser_headers):
    """Scenario 3: Hospital Admin generates Floors, Wards, Rooms, Beds, and OTs in one atomic call."""
    res = client.post(
        "/v1/api/admin/spatial-matrix",
        headers=superuser_headers,
        json={
            "floors": [
                {
                    "floor_number": 20,
                    "name": "Twentieth Floor - Executive Wing",
                    "wards": [
                        {
                            "code": "W_EXEC_20",
                            "name": "Executive Suite Ward",
                            "ward_type": "PRIVATE",
                            "department_code": "DEPT_GEN_MED",
                            "rooms": [
                                {
                                    "room_number_prefix": "20",
                                    "rooms_count": 3,
                                    "room_type": "DELUXE",
                                    "beds": {
                                        "prefix": "SUITE",
                                        "start_number": 1,
                                        "count": 2,
                                        "bed_type": "MOTORIZED",
                                        "base_daily_rate_paise": 600000,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
            "operation_theatres": [
                {
                    "code": "OT_ROBOTIC_1",
                    "name": "DaVinci Robotic Suite 1",
                    "theatre_type": "MODULAR_MAJOR",
                    "hourly_rate_paise": 1000000,
                }
            ],
        },
    )
    assert res.status_code == 201
    summary = res.json()["summary"]
    assert summary == {"floors": 1, "wards": 1, "rooms": 3, "beds": 6, "theatres": 1}


def test_04_tenant_admin_hospital_policy_and_federation_sovereignty_configurator(client, superuser_headers):
    """Scenario 4: Hospital Admin reads and updates clinical thresholds and federation data-sharing sovereignty."""
    get_res = client.get("/v1/api/admin/policies", headers=superuser_headers)
    assert get_res.status_code == 200
    assert get_res.json()["max_failed_login_attempts"] == 5
    assert get_res.json()["federation_sharing_mode"] == "FULL_MESH"

    put_res = client.put(
        "/v1/api/admin/policies",
        headers=superuser_headers,
        json={
            "max_failed_login_attempts": 7,
            "default_consultation_duration_minutes": 20,
            "enforce_financial_discharge_gate": False,
            "low_stock_threshold_units": 50,
            "federation_sharing_mode": "CORPORATE_GROUP_ONLY",
            "federation_share_demographics": True,
            "federation_share_allergies": True,
        },
    )
    assert put_res.status_code == 200
    assert put_res.json()["max_failed_login_attempts"] == 7
    assert put_res.json()["enforce_financial_discharge_gate"] is False
    assert put_res.json()["federation_sharing_mode"] == "CORPORATE_GROUP_ONLY"


def test_05_tenant_admin_bulk_import_service_catalog_and_medications(client, superuser_headers):
    """Scenario 5: Hospital Admin bulk-imports Service Catalog and Pharmacy Formulary via JSON/CSV."""
    cat = client.post(
        "/v1/api/diagnostics/categories",
        headers=superuser_headers,
        json={"department_id": 1, "code": "CAT_BULK", "name": "Bulk Imported Diagnostics"},
    ).json()

    services_json = json.dumps([
        {"category_id": cat["id"], "code": "BLK_PET_CT", "name": "Whole Body PET-CT", "service_type": "RADIOLOGY", "base_price_paise": 1500000},
        {"category_id": cat["id"], "code": "BLK_DEXA", "name": "Bone Mineral Density (DEXA)", "service_type": "RADIOLOGY", "base_price_paise": 350000},
    ]).encode("utf-8")

    svc_res = client.post(
        "/v1/api/admin/bulk-import/service-catalog",
        headers=superuser_headers,
        files={"file": ("services.json", io.BytesIO(services_json), "application/json")},
    )
    assert svc_res.status_code == 200
    assert svc_res.json()["success_count"] == 2

    meds_csv = (
        "code,brand_name,generic_name,form,strength,low_stock_threshold_units,fefo_expiry_warning_days\n"
        "BLK_ATOR_20,Lipitor-20,Atorvastatin,TABLET,20mg,8,60\n"
        "BLK_MET_500,Glycomet-500,Metformin,TABLET,500mg\n"
    ).encode("utf-8")

    med_res = client.post(
        "/v1/api/admin/bulk-import/medications",
        headers=superuser_headers,
        files={"file": ("meds.csv", io.BytesIO(meds_csv), "text/csv")},
    )
    assert med_res.status_code == 200
    assert med_res.json()["success_count"] == 2
    medications = client.get(
        "/v1/api/pharmacy/medications", headers=superuser_headers
    )
    assert medications.status_code == 200
    atorvastatin = next(
        item for item in medications.json()["items"] if item["code"] == "BLK_ATOR_20"
    )
    assert atorvastatin["low_stock_threshold_units"] == 8
    assert atorvastatin["fefo_expiry_warning_days"] == 60


def test_06_control_plane_tenant_inspector_federation_topology_and_audit_ledger(client, doctor_headers):
    """Scenario 6: Verify Tenant Inspector details, Zero-Knowledge Federation Topology, and Control Plane Audit Ledger."""
    cp = _get_cp_client(client)
    master_key = settings.MASTER_ONBOARDING_KEY

    det_res = cp.get("/api/tenants/apollo/details", headers={"X-Master-Setup-Key": master_key})
    assert det_res.status_code == 200
    assert "enabled_modules" in det_res.json()
    assert "metrics" in det_res.json()

    topo_res = cp.get("/api/federation/topology", headers={"X-Master-Setup-Key": master_key})
    assert topo_res.status_code == 200
    assert "nodes" in topo_res.json()

    aud_res = cp.get("/api/fleet/audit-logs", headers={"X-Master-Setup-Key": master_key})
    assert aud_res.status_code == 200
    assert "items" in aud_res.json()
    page_res = cp.get(
        "/api/fleet/audit-logs?limit=1&offset=0&target=apollo",
        headers={"X-Master-Setup-Key": master_key},
    )
    assert page_res.status_code == 200
    page_data = page_res.json()
    assert page_data["limit"] == 1
    assert page_data["offset"] == 0
    assert page_data["total"] >= len(page_data["items"])
    assert len(page_data["items"]) <= 1
    assert all("apollo" in item["target_slug"].lower() for item in page_data["items"])

def test_07_federation_privacy_sovereignty_patient_opt_out_and_hospital_isolated_silo(
    client, apollo_admin_headers, sunrise_admin_headers
):
    """
    Scenario 7:
    1. Register a confidential VIP patient at Apollo with allow_network_sharing=False ->
       Sunrise Hospital cannot discover them (404).
    2. Register a standard patient at Apollo, then switch Apollo's policy to ISOLATED_SILO ->
       Sunrise Hospital is blocked from discovering Apollo's patients (404).
    """
    sun_client = client.post(
        "/v1/api/federation/clients",
        headers=sunrise_admin_headers,
        json={"branch_code": "SUN-PRIV-01", "name": "Sunrise Privacy Test Node"},
    ).json()
    fed_headers = {"X-Federation-API-Key": sun_client["raw_api_key"]}

    # 1. Patient-level privacy opt-out (allow_network_sharing=False)
    vip_res = client.post(
        "/v1/api/patients",
        headers=apollo_admin_headers,
        json={
            "full_name": "Confidential VIP Patient",
            "date_of_birth": "1980-05-05T00:00:00Z",
            "gender": "FEMALE",
            "phone_number": "9111199999",
            "allow_network_sharing": False,
        },
    )
    assert vip_res.status_code == 201
    assert vip_res.json()["allow_network_sharing"] is False

    disc_vip = client.get(
        "/v1/api/federation/patients/discover?phone_number=9111199999",
        headers=fed_headers,
    )
    assert disc_vip.status_code == 404

    # 2. Hospital-level ISOLATED_SILO mode
    std_p = client.post(
        "/v1/api/patients",
        headers=apollo_admin_headers,
        json={
            "full_name": "Silo Protected Patient",
            "date_of_birth": "1985-08-08T00:00:00Z",
            "gender": "MALE",
            "phone_number": "9222288888",
            "allow_network_sharing": True,
        },
    ).json()

    # Before ISOLATED_SILO: discoverable (200)
    assert (
        client.get(
            "/v1/api/federation/patients/discover?phone_number=9222288888",
            headers=fed_headers,
        ).status_code
        == 200
    )

    # Apollo Hospital Admin switches policy to ISOLATED_SILO
    client.put(
        "/v1/api/admin/policies",
        headers=apollo_admin_headers,
        json={"federation_sharing_mode": "ISOLATED_SILO"},
    )

    # After ISOLATED_SILO: Apollo refuses outbound sharing (404)
    assert (
        client.get(
            "/v1/api/federation/patients/discover?phone_number=9222288888",
            headers=fed_headers,
        ).status_code
        == 404
    )

def test_08_tenant_admin_dynamic_spatial_reconfiguration_and_occupancy_guards(
    client, superuser_headers, doctor_headers
):
    """
    Scenario 8:
    1. Create Floor, 2 Wards, 2 Rooms, and 2 Beds via /v1/api/ip/... endpoints.
    2. Admit an inpatient into Bed 1 (marks Bed 1 as OCCUPIED).
    3. Verify Room 2 can be shifted to Ward Beta (PUT /v1/api/ip/rooms/{id}) and Bed 2 can be
       shifted to Room 2, upgraded to ICU, and repriced (PATCH /v1/api/ip/beds/{id}).
    4. Verify shifting or deleting Bed 1 (OCCUPIED) or deleting Room 1 is blocked with 409 Conflict.
    5. Verify deleting unoccupied Bed 2 and empty Room 2 succeeds cleanly (200 OK).
    """
    f_id = client.post(
        "/v1/api/ip/floors",
        headers=superuser_headers,
        json={"floor_number": 88, "name": "Dynamic Reconfig Floor"},
    ).json()["floor_id"]

    w_a_id = client.post(
        "/v1/api/ip/wards",
        headers=superuser_headers,
        json={
            "floor_id": f_id,
            "department_id": 1,
            "code": "W_DYN_A",
            "name": "Dynamic Ward Alpha",
            "ward_type": "GENERAL",
        },
    ).json()["id"]

    w_b_id = client.post(
        "/v1/api/ip/wards",
        headers=superuser_headers,
        json={
            "floor_id": f_id,
            "department_id": 1,
            "code": "W_DYN_B",
            "name": "Dynamic Ward Beta",
            "ward_type": "ICU",
        },
    ).json()["id"]

    r1_id = client.post(
        "/v1/api/ip/rooms",
        headers=superuser_headers,
        json={
            "ward_id": w_a_id,
            "room_number": "DYN-101",
            "room_type": "DOUBLE",
            "bed_capacity": 2,
        },
    ).json()["room_id"]

    r2_id = client.post(
        "/v1/api/ip/rooms",
        headers=superuser_headers,
        json={
            "ward_id": w_a_id,
            "room_number": "DYN-102",
            "room_type": "SINGLE",
            "bed_capacity": 2,
        },
    ).json()["room_id"]

    bed_occ_id = client.post(
        "/v1/api/ip/beds",
        headers=superuser_headers,
        json={
            "room_id": r1_id,
            "bed_number": "DYN-B1",
            "bed_type": "STANDARD",
            "base_daily_rate_paise": 150000,
        },
    ).json()["bed_id"]

    bed_free_id = client.post(
        "/v1/api/ip/beds",
        headers=superuser_headers,
        json={
            "room_id": r1_id,
            "bed_number": "DYN-B2",
            "bed_type": "STANDARD",
            "base_daily_rate_paise": 150000,
        },
    ).json()["bed_id"]

    # 2. Admit a patient into bed_occ_id so it becomes OCCUPIED
    patient = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Spatial Guard Patient",
            "date_of_birth": "1975-03-15T00:00:00Z",
            "gender": "MALE",
            "phone_number": "9333344444",
        },
    ).json()

    enc = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={
            "patient_id": patient["id"],
            "encounter_type": "IPD",
            "primary_doctor_id": 2,
            "department_id": 1,
            "chief_complaint_notes": "Spatial occupancy guard verification",
        },
    ).json()

    adm_res = client.post(
        "/v1/api/adt/admissions",
        headers=doctor_headers,
        json={
            "encounter_id": enc["id"],
            "admission_type": "ROUTINE",
            "admitting_doctor_id": 2,
            "assigned_bed_id": bed_occ_id,
        },
    )
    assert adm_res.status_code == 201

    # 3. Shift Room 2 from Ward Alpha to Ward Beta & upgrade to DELUXE via PUT /v1/api/ip/rooms/{id}
    shift_room_res = client.put(
        f"/v1/api/ip/rooms/{r2_id}",
        headers=superuser_headers,
        json={"ward_id": w_b_id, "room_type": "DELUXE", "bed_capacity": 4},
    )
    assert shift_room_res.status_code == 200
    assert shift_room_res.json()["ward_id"] == w_b_id
    assert shift_room_res.json()["room_type"] == "DELUXE"

    # 4. Shift unoccupied Bed 2 from Room 1 to Room 2, upgrade to ICU, and update daily rate via PATCH /v1/api/ip/beds/{id}
    shift_bed_res = client.patch(
        f"/v1/api/ip/beds/{bed_free_id}",
        headers=superuser_headers,
        json={
            "room_id": r2_id,
            "bed_number": "DYN-ICU-B2",
            "bed_type": "ICU",
            "base_daily_rate_paise": 750000,
        },
    )
    assert shift_bed_res.status_code == 200
    assert shift_bed_res.json()["room_id"] == r2_id
    assert shift_bed_res.json()["bed_type"] == "ICU"
    assert shift_bed_res.json()["base_daily_rate_paise"] == 750000

    # 5. Attempting to shift or delete the OCCUPIED bed or Room 1 must be blocked with 409 Conflict
    assert (
        client.patch(
            f"/v1/api/ip/beds/{bed_occ_id}",
            headers=superuser_headers,
            json={"room_id": r2_id},
        ).status_code
        == 409
    )
    assert client.delete(f"/v1/api/ip/beds/{bed_occ_id}", headers=superuser_headers).status_code == 409
    assert client.delete(f"/v1/api/ip/rooms/{r1_id}", headers=superuser_headers).status_code == 409

    # 6. Deactivating unoccupied Bed 2 and empty Room 2 succeeds cleanly (200 OK)
    assert client.delete(f"/v1/api/ip/beds/{bed_free_id}", headers=superuser_headers).status_code == 200
    assert client.delete(f"/v1/api/ip/rooms/{r2_id}", headers=superuser_headers).status_code == 200

def test_09_control_plane_bed_and_user_quota_enforcement(client, apollo_admin_headers):
    """
    Scenario 9:
    1. Set 'apollo' hospital quota to max_beds=2 via Control Plane /api/tenants/apollo/entitlements.
    2. Create Floor -> Ward -> Room and 2 Beds in 'apollo' -> Succeeds (201 Created).
    3. Attempt to create a 3rd Bed in 'apollo' -> Blocked with 403 Commercial Quota Exceeded!
    4. Upgrade 'apollo' quota to max_beds=5 in Control Plane -> Creating 3rd Bed now succeeds (201 Created)!
    """
    cp = _get_cp_client(client)

    # 1. Cap 'apollo' at max_beds = 2
    ent_res = cp.patch(
        "/api/tenants/apollo/entitlements",
        json={
            "master_setup_key": settings.MASTER_ONBOARDING_KEY,
            "pricing_model": "BUNDLE_TOTAL_HOSPITAL",
            "billing_cycle": "ANNUAL_LICENSE",
            "enabled_modules": {"ipd_adt": True, "opd": True},
            "quota_limits": {"max_beds": 2, "max_users": 10},
            "maintenance_mode": False,
        },
    )
    assert ent_res.status_code == 200

    # 2. Provision Floor, Ward, Room, and 2 Beds in Apollo (up to the 2-bed cap)
    f_id = client.post(
        "/v1/api/ip/floors",
        headers=apollo_admin_headers,
        json={"floor_number": 1, "name": "Quota Test Floor"},
    ).json()["floor_id"]

    w_id = client.post(
        "/v1/api/ip/wards",
        headers=apollo_admin_headers,
        json={"floor_id": f_id, "department_id": 1, "code": "W_QUOTA", "name": "Quota Ward", "ward_type": "GENERAL"},
    ).json()["id"]

    r_id = client.post(
        "/v1/api/ip/rooms",
        headers=apollo_admin_headers,
        json={"ward_id": w_id, "room_number": "Q-101", "room_type": "DORMITORY", "bed_capacity": 5},
    ).json()["room_id"]

    assert (
        client.post(
            "/v1/api/ip/beds",
            headers=apollo_admin_headers,
            json={"room_id": r_id, "bed_number": "Q-B1", "bed_type": "STANDARD", "base_daily_rate_paise": 100000},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/v1/api/ip/beds",
            headers=apollo_admin_headers,
            json={"room_id": r_id, "bed_number": "Q-B2", "bed_type": "STANDARD", "base_daily_rate_paise": 100000},
        ).status_code
        == 201
    )

    # 3. 3rd bed exceeds max_beds=2 -> Blocked with 403 Forbidden!
    exceed_res = client.post(
        "/v1/api/ip/beds",
        headers=apollo_admin_headers,
        json={"room_id": r_id, "bed_number": "Q-B3", "bed_type": "STANDARD", "base_daily_rate_paise": 100000},
    )
    assert exceed_res.status_code == 403
    assert "quota exceeded" in exceed_res.json()["message"].lower()

    # 4. Upgrade 'apollo' quota to max_beds=5 in Control Plane -> 3rd bed now succeeds!
    cp.patch(
        "/api/tenants/apollo/entitlements",
        json={
            "master_setup_key": settings.MASTER_ONBOARDING_KEY,
            "pricing_model": "BUNDLE_TOTAL_HOSPITAL",
            "billing_cycle": "ANNUAL_LICENSE",
            "enabled_modules": {"ipd_adt": True, "opd": True},
            "quota_limits": {"max_beds": 5, "max_users": 10},
            "maintenance_mode": False,
        },
    )
    assert (
        client.post(
            "/v1/api/ip/beds",
            headers=apollo_admin_headers,
            json={"room_id": r_id, "bed_number": "Q-B3", "bed_type": "STANDARD", "base_daily_rate_paise": 100000},
        ).status_code
        == 201
    )