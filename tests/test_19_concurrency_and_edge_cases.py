from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from backend.control_plane.models import GlobalPatientPointer
from backend.domains.billing.models import Invoice

def test_01_optimistic_concurrency_control_on_invoices(client, doctor_headers, superuser_headers, multi_tenant_env):
    """Scenario 1: OCC prevents 'lost updates' when concurrent transactions target the same invoice."""
    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "OCC Patient", "date_of_birth": "1980-01-01T00:00:00Z", "gender": "MALE"}).json()
    inv = client.post("/v1/api/billing/invoices", headers=superuser_headers, json={"patient_id": p["id"]}).json()
    
    DefaultSession = sessionmaker(bind=multi_tenant_env["tenant_engines"]["hms_default"])
    
    with DefaultSession() as session1, DefaultSession() as session2:
        # Both concurrent database sessions read version 1 of the invoice
        inv_s1 = session1.get(Invoice, inv["id"])
        inv_s2 = session2.get(Invoice, inv["id"])
        
        # Transaction 1 applies an update and commits, successfully incrementing the version_id to 2
        inv_s1.notes = "Update from Transaction 1"
        session1.commit()
        
        # Transaction 2 attempts to apply an update using the stale version 1
        inv_s2.notes = "Update from Transaction 2"
        try:
            session2.commit()
            assert False, "Optimistic Concurrency Control failed; StaleDataError was not raised."
        except StaleDataError:
            session2.rollback()
            assert True

def test_02_pharmacy_fefo_enforcement(client, doctor_headers, pharmacist_headers):
    """Scenario 2: Dispensing newer stock while older stock exists triggers 409 FEFO Hard-Stop."""
    med = client.post("/v1/api/pharmacy/medications", headers=pharmacist_headers, json={"code": "MED_FEFO", "brand_name": "FEFO-Test", "generic_name": "Drug", "form": "TABLET", "strength": "10mg"}).json()

    now = datetime.now(timezone.utc)
    batch_a = client.post("/v1/api/pharmacy/inventory/batches", headers=pharmacist_headers, json={"medication_id": med["id"], "batch_number": "BA-30D", "expiry_date": (now + timedelta(days=30)).isoformat(), "stock_quantity": 50, "unit_price_paise": 100}).json()
    batch_b = client.post("/v1/api/pharmacy/inventory/batches", headers=pharmacist_headers, json={"medication_id": med["id"], "batch_number": "BB-90D", "expiry_date": (now + timedelta(days=90)).isoformat(), "stock_quantity": 50, "unit_price_paise": 100}).json()

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "FEFO Patient", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "FEMALE"}).json()
    enc = client.post("/v1/api/adt/encounters", headers=doctor_headers, json={"patient_id": p["id"], "encounter_type": "OPD", "primary_doctor_id": 2, "department_id": 1}).json()
    rx = client.post("/v1/api/pharmacy/prescriptions", headers=doctor_headers, json={"encounter_id": enc["id"], "items": [{"medication_id": med["id"], "dosage_instruction": "OD", "requested_quantity": 10}]}).json()

    # Reject newer batch because older stock (Batch A) is available
    fefo_fail = client.post(
        f"/v1/api/pharmacy/prescriptions/{rx['id']}/dispense",
        headers=pharmacist_headers,
        json={"items": [{"prescription_item_id": rx["items"][0]["id"], "batch_id": batch_b["id"], "quantity_to_dispense": 10}]}
    )
    assert fefo_fail.status_code == 409
    assert "FEFO Violation" in fefo_fail.json().get("detail", fefo_fail.json().get("message", ""))

    # Simulate Administrative Override by exhausting the older stock (since API schema strips extra Pydantic kwargs)
    client.patch(
        f"/v1/api/pharmacy/inventory/batches/{batch_a['id']}/adjust",
        headers=pharmacist_headers,
        json={"quantity_adjustment": -50}
    )

    # Now dispensing the newer batch succeeds with 200 OK
    fefo_override = client.post(
        f"/v1/api/pharmacy/prescriptions/{rx['id']}/dispense",
        headers=pharmacist_headers,
        json={"items": [{"prescription_item_id": rx["items"][0]["id"], "batch_id": batch_b["id"], "quantity_to_dispense": 10}]}
    )
    assert fefo_override.status_code == 200


def test_03_platelets_cryoprecipitate_exact_match_validation(client, doctor_headers, superuser_headers):
    """Scenario 3: Non-PRBC blood components strictly require exact 1:1 ABO/Rh match."""
    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Platelet Patient", "date_of_birth": "1985-01-01T00:00:00Z", "gender": "MALE", "blood_group": "AB+"}).json()
    enc = client.post("/v1/api/adt/encounters", headers=doctor_headers, json={"patient_id": p["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1}).json()

    bag = client.post(
        "/v1/api/blood-bank/bags",
        headers=superuser_headers,
        json={
            "bag_number": "PLT-ONEG-001",
            "component_type": "PLATELETS",
            "blood_group": "O-",
            "volume_ml": 250,
            "collection_date": datetime.now(timezone.utc).isoformat(),
            "expiry_date": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        }
    ).json()

    cm_fail = client.post(
        "/v1/api/blood-bank/cross-match",
        headers=superuser_headers,
        json={"encounter_id": enc["id"], "blood_bag_id": bag["id"], "ordered_by_doctor_id": 2}
    )
    assert cm_fail.status_code == 409
    assert "Exact match required" in cm_fail.json().get("detail", cm_fail.json().get("message", ""))


def test_04_federation_pointer_purge_on_deactivation(client, superuser_headers, multi_tenant_env):
    """Scenario 4: Soft-deleting a patient completely purges their GlobalPatientPointer from the MPI mesh."""
    p = client.post("/v1/api/patients", headers=superuser_headers, json={"full_name": "Mesh Purge Target", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE", "phone_number": "9988771122"}).json()

    ControlSession = multi_tenant_env["control_session_factory"]
    with ControlSession() as cdb:
        ptr = cdb.query(GlobalPatientPointer).filter(GlobalPatientPointer.source_mrn == p["mrn"]).first()
        assert ptr is not None

    client.delete(f"/v1/api/patients/{p['id']}", headers=superuser_headers)

    with ControlSession() as cdb:
        ptr_after = cdb.query(GlobalPatientPointer).filter(GlobalPatientPointer.source_mrn == p["mrn"]).first()
        assert ptr_after is None


def test_05_complex_insurance_adjudication_math(client, superuser_headers, doctor_headers, billing_headers):
    """Scenario 5: Ensures TPA calculations respect room caps, exclusions, and discretionary discounts simultaneously."""
    provider = client.post("/v1/api/insurance/providers", headers=superuser_headers, json={"code": "TPA_COMPLEX", "name": "Complex TPA", "provider_type": "TPA"}).json()
    plan = client.post("/v1/api/insurance/plans", headers=superuser_headers, json={"provider_id": provider["id"], "code": "PLAN_COMPLEX", "name": "Complex Plan", "default_copay_percentage": 0, "max_room_rent_daily_paise": 300000}).json()
    
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_COMPLEX", "name": "Complex Cat"}).json()
    kit_svc = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "SVC_KIT", "name": "Admission Kit", "service_type": "PROCEDURE", "base_price_paise": 150000}).json()
    client.post(f"/v1/api/insurance/plans/{plan['id']}/tariffs", headers=superuser_headers, json={"service_id": kit_svc["id"], "tariff_price_paise": 150000, "is_covered": False})

    f_id = client.post("/v1/api/ip/floors", headers=superuser_headers, json={"floor_number": 99, "name": "Floor 99"}).json()["floor_id"]
    w_id = client.post("/v1/api/ip/wards", headers=superuser_headers, json={"floor_id": f_id, "department_id": 1, "code": "W_COMPLEX", "name": "Complex Ward", "ward_type": "GENERAL"}).json()["id"]
    r_id = client.post("/v1/api/ip/rooms", headers=superuser_headers, json={"ward_id": w_id, "room_number": "9901", "room_type": "SINGLE", "bed_capacity": 1}).json()["room_id"]
    bed_id = client.post("/v1/api/ip/beds", headers=superuser_headers, json={"room_id": r_id, "bed_number": "9901-A", "bed_type": "STANDARD", "base_daily_rate_paise": 500000}).json()["bed_id"]

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Complex Math", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"}).json()
    enc = client.post("/v1/api/adt/encounters", headers=doctor_headers, json={"patient_id": p["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1}).json()
    
    adm = client.post("/v1/api/adt/admissions", headers=doctor_headers, json={"encounter_id": enc["id"], "admission_type": "ROUTINE", "admitting_doctor_id": 2, "assigned_bed_id": bed_id}).json()
    policy = client.post("/v1/api/insurance/policies", headers=superuser_headers, json={"patient_id": p["id"], "provider_id": provider["id"], "plan_id": plan["id"], "policy_number": "POL-123", "member_id": "MEM-123", "sum_insured_paise": 50000000, "valid_from": "2020-01-01T00:00:00Z", "valid_till": "2030-01-01T00:00:00Z"}).json()

    inv = client.post("/v1/api/billing/invoices", headers=billing_headers, json={"patient_id": p["id"], "encounter_id": enc["id"]}).json()
    client.post(f"/v1/api/billing/invoices/{inv['id']}/items", headers=billing_headers, json={"service_id": kit_svc["id"], "description": "Admission Kit", "quantity": 1, "unit_price_paise": 150000})
    client.post(f"/v1/api/adt/admissions/{adm['id']}/discharge", headers=superuser_headers, json={"discharge_disposition": "ROUTINE_DISCHARGE", "discharge_summary": "Discharged"})

    client.patch(f"/v1/api/billing/invoices/{inv['id']}/discount", headers=superuser_headers, json={"discount_amount_paise": 50000, "discount_reason": "Admin concession"})
    client.patch(f"/v1/api/billing/invoices/{inv['id']}/finalize", headers=superuser_headers)

    claim = client.post("/v1/api/insurance/claims", headers=superuser_headers, json={"invoice_id": inv["id"], "policy_id": policy["id"]}).json()

    # Gross: 650000 -> Discount: 50000 -> Net Payable: 600000
    # Disallowed: 150000 (Kit) + 200000 (Bed Excess) = 350000 -> Insurer Payable: 250000
    assert claim["insurer_payable_paise"] == 250000
    assert claim["patient_copay_paise"] == 350000
    assert claim["claimed_amount_paise"] == 600000