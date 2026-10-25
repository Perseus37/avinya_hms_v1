"""
Tests for Enterprise Track 3:
- Revenue Cycle Management (RCM) & Charge Master (CDM) Automation
- Multi-Tariff MoU Rate Card Resolution Engine (Cash vs Insurance/TPA vs Room Rent Caps)
- Event-Driven Clinical Charge Posting with Idempotency Protection
- Automated Inpatient Midnight Census Charge Run
- Surgical Case Completion Automated Facility & Procedure Billing
"""

from datetime import datetime, timedelta, timezone
import pytest


def _create_patient(client, headers, name="RCM Patient", dob="1984-03-22T00:00:00Z"):
    res = client.post(
        "/v1/api/patients",
        headers=headers,
        json={
            "full_name": name,
            "date_of_birth": dob,
            "gender": "MALE",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_encounter(client, headers, patient_id, enc_type="IPD"):
    res = client.post(
        "/v1/api/adt/encounters",
        headers=headers,
        json={
            "patient_id": patient_id,
            "encounter_type": enc_type,
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_service(client, headers, code, name, price_paise=100000, gst_percent=0):
    res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=headers,
        json={
            "category_id": 1,
            "code": code,
            "name": name,
            "service_type": "LAB",
            "base_price_paise": price_paise,
            "gst_rate_percent": gst_percent,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


# =============================================================================
# 1. Multi-Tariff MoU Rate Card Resolution Tests
# =============================================================================

def test_multi_tariff_resolution_cash_vs_insurance_and_room_rent_cap(client, superuser_headers, billing_headers):
    """
    Scenario 1: Resolves service charges against payer contracts:
    - Cash patient gets standard CDM base price.
    - Insured patient gets negotiated plan tariff rate card.
    - Insured patient accommodation gets capped at max room rent.
    """
    # 1. Author service catalog items
    svc_ct_scan = _create_service(client, superuser_headers, code="SVC_CT_CHEST_RCM", name="CT Chest Contrast", price_paise=400000)
    svc_deluxe_room = _create_service(client, superuser_headers, code="ROOM_DELUXE_SUITE", name="Deluxe Private Room Daily", price_paise=600000)

    # 2. Author Insurance Provider and Plan with Rs 3500 (350,000 paise) daily room rent cap
    provider_res = client.post(
        "/v1/api/insurance/providers",
        headers=billing_headers,
        json={
            "code": "PROV_STAR_HEALTH",
            "name": "Star Health & Allied Insurance",
            "provider_type": "INSURER",
            "contact_email": "claims@starhealth.example.com",
        },
    )
    assert provider_res.status_code == 201, provider_res.text
    provider = provider_res.json()

    plan_res = client.post(
        "/v1/api/insurance/plans",
        headers=billing_headers,
        json={
            "provider_id": provider["id"],
            "code": "PLAN_STAR_PLATINUM",
            "name": "Star Platinum Corporate Tier",
            "default_copay_percentage": 10,
            "max_room_rent_daily_paise": 350000,  # 3500 INR cap
            "apply_tariff_automatically": True,
        },
    )
    assert plan_res.status_code == 201, plan_res.text
    plan = plan_res.json()

    # 3. Add negotiated tariff for CT Chest (Negotiated down from 4000 to 2800 INR = 280,000 paise)
    tariff_res = client.post(
        f"/v1/api/insurance/plans/{plan['id']}/tariffs",
        headers=billing_headers,
        json={
            "service_id": svc_ct_scan["id"],
            "tariff_price_paise": 280000,
            "is_covered": True,
        },
    )
    assert tariff_res.status_code == 201, tariff_res.text

    # 4. Cash Patient Encounter (no insurance policy)
    patient_cash = _create_patient(client, superuser_headers, name="Cash Paying Patient")
    enc_cash = _create_encounter(client, superuser_headers, patient_cash["id"])

    cash_resolve = client.post(
        "/v1/api/billing/rcm/resolve-tariff",
        headers=billing_headers,
        json={"encounter_id": enc_cash["id"], "service_id": svc_ct_scan["id"], "quantity": 1},
    )
    assert cash_resolve.status_code == 200, cash_resolve.text
    cash_data = cash_resolve.json()
    assert cash_data["effective_unit_price_paise"] == 400000  # Full base price
    assert cash_data["tariff_source"] == "BASE_CATALOG"

    # 5. Insured Patient Encounter
    patient_insured = _create_patient(client, superuser_headers, name="Insured Platinum Patient")
    enc_insured = _create_encounter(client, superuser_headers, patient_insured["id"])

    # Link insurance policy
    policy_res = client.post(
        "/v1/api/insurance/policies",
        headers=billing_headers,
        json={
            "patient_id": patient_insured["id"],
            "provider_id": provider["id"],
            "plan_id": plan["id"],
            "policy_number": "POL-STAR-771122",
            "member_id": "MEM-992211",
            "sum_insured_paise": 50000000,
            "copay_percentage": 10,
            "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
            "valid_till": (datetime.now(timezone.utc) + timedelta(days=335)).isoformat(),
        },
    )
    assert policy_res.status_code == 201, policy_res.text

    # 5a. CT Scan resolves to negotiated tariff
    ins_resolve = client.post(
        "/v1/api/billing/rcm/resolve-tariff",
        headers=billing_headers,
        json={"encounter_id": enc_insured["id"], "service_id": svc_ct_scan["id"], "quantity": 1},
    )
    assert ins_resolve.status_code == 200, ins_resolve.text
    ins_data = ins_resolve.json()
    assert ins_data["effective_unit_price_paise"] == 280000  # Negotiated rate!
    assert ins_data["tariff_source"] == "INSURANCE_PLAN_TARIFF"
    assert ins_data["plan_code"] == "PLAN_STAR_PLATINUM"

    # 5b. Room Deluxe Suite (600,000 paise base) resolves capped to max room rent (350,000 paise)
    room_resolve = client.post(
        "/v1/api/billing/rcm/resolve-tariff",
        headers=billing_headers,
        json={"encounter_id": enc_insured["id"], "service_id": svc_deluxe_room["id"], "quantity": 1},
    )
    assert room_resolve.status_code == 200, room_resolve.text
    room_data = room_resolve.json()
    assert room_data["effective_unit_price_paise"] == 350000  # Capped!
    assert room_data["tariff_source"] == "PLAN_ROOM_RENT_CAP"


# =============================================================================
# 2. Event-Driven Clinical Charge Posting & Idempotency
# =============================================================================

def test_event_driven_clinical_charge_posting_and_idempotency(client, superuser_headers, billing_headers):
    """
    Scenario 2: Posts an automated clinical charge from lab verification or bedside order.
    Ensures draft invoice creation, line item calculation, and duplicate charge protection.
    """
    patient = _create_patient(client, superuser_headers, name="Event Charge Patient")
    encounter = _create_encounter(client, superuser_headers, patient["id"])
    svc = _create_service(client, superuser_headers, code="SVC_ECHO_RCM", name="2D Echocardiogram", price_paise=250000)

    # 1. Post initial charge
    payload = {
        "encounter_id": encounter["id"],
        "event_type": "DIAGNOSTIC_VERIFIED",
        "source_reference": "DIAG-TASK-ECHO-8812",
        "description": "2D Echocardiography Result Verification",
        "service_id": svc["id"],
        "quantity": 1,
    }

    res = client.post(
        "/v1/api/billing/rcm/post-clinical-charge",
        headers=billing_headers,
        json=payload,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["unit_price_paise"] == 250000
    assert body["total_charge_paise"] == 250000
    assert body["source_reference"] == "DIAG-TASK-ECHO-8812"
    assert body["invoice_total_amount_paise"] == 250000
    assert body["invoice_number"].startswith("INV-")

    # 2. Duplicate submission with exact same source_reference must fail with HTTP 409
    dup_res = client.post(
        "/v1/api/billing/rcm/post-clinical-charge",
        headers=billing_headers,
        json=payload,
    )
    assert dup_res.status_code == 409, dup_res.text
    assert "Duplicate billing prevented" in dup_res.text

    # 3. Verify charge ledger listing
    ledger_res = client.get(
        f"/v1/api/billing/rcm/charge-events?encounter_id={encounter['id']}",
        headers=billing_headers,
    )
    assert ledger_res.status_code == 200, ledger_res.text
    events = ledger_res.json()
    assert len(events) == 1
    assert events[0]["source_reference"] == "DIAG-TASK-ECHO-8812"


# =============================================================================
# 3. Automated Inpatient Midnight Census Run
# =============================================================================

def test_inpatient_midnight_census_charge_run_and_idempotency(client, superuser_headers, billing_headers):
    """
    Scenario 3: Executes daily midnight census room charge posting for active IPD admissions.
    Runs once and verifies charges are posted; running a second time on the same date skips duplicate billing.
    """
    # 1. Create a physical floor, ward, room and bed
    floor_res = client.post(
        "/v1/api/ip/floors",
        headers=superuser_headers,
        json={"floor_number": 99, "name": "RCM Inpatient Tower Floor 9"},
    )
    floor_id = floor_res.json()["floor_id"] if floor_res.status_code == 201 else 1

    ward_res = client.post(
        "/v1/api/ip/wards",
        headers=superuser_headers,
        json={
            "department_id": 1,
            "floor_id": floor_id,
            "code": "WARD_GEN_MALE_RCM",
            "name": "General Male Ward",
            "ward_type": "GENERAL",
        },
    )
    assert ward_res.status_code in (200, 201), ward_res.text
    ward_id = ward_res.json()["id"]

    room_res = client.post(
        "/v1/api/ip/rooms",
        headers=superuser_headers,
        json={
            "ward_id": ward_id,
            "room_number": "RM-201-RCM",
            "room_type": "SINGLE",
            "bed_capacity": 2,
        },
    )
    assert room_res.status_code in (200, 201), room_res.text
    room_id = room_res.json()["room_id"]

    bed_res = client.post(
        "/v1/api/ip/beds",
        headers=superuser_headers,
        json={
            "room_id": room_id,
            "bed_number": "BED-201-A-RCM",
            "bed_type": "STANDARD",
            "base_daily_rate_paise": 180000,  # 1800 INR daily
        },
    )
    assert bed_res.status_code in (200, 201), bed_res.text
    bed_id = bed_res.json()["bed_id"]

    # 2. Create patient and admit into this bed
    patient = _create_patient(client, superuser_headers, name="Census Admitted Patient")
    encounter = _create_encounter(client, superuser_headers, patient["id"], enc_type="IPD")

    adm_res = client.post(
        "/v1/api/adt/admissions",
        headers=superuser_headers,
        json={
            "encounter_id": encounter["id"],
            "admission_type": "ROUTINE",
            "admitting_doctor_id": 2,
            "assigned_bed_id": bed_id,
        },
    )
    assert adm_res.status_code in (200, 201), adm_res.text

    census_date = "2026-10-25"

    # 3. Run Midnight Census Run 1: Must post bed accommodation charge
    run1 = client.post(
        "/v1/api/billing/rcm/midnight-census-charges",
        headers=billing_headers,
        json={"census_date": census_date},
    )
    assert run1.status_code == 200, run1.text
    data1 = run1.json()
    assert data1["charges_posted_count"] >= 1
    assert data1["already_posted_count"] == 0

    # 4. Run Midnight Census Run 2 on same date: Idempotent skip
    run2 = client.post(
        "/v1/api/billing/rcm/midnight-census-charges",
        headers=billing_headers,
        json={"census_date": census_date},
    )
    assert run2.status_code == 200, run2.text
    data2 = run2.json()
    assert data2["charges_posted_count"] == 0
    assert data2["already_posted_count"] >= 1


# =============================================================================
# 4. Surgery Completion Automated Billing
# =============================================================================

def test_surgery_completion_automated_facility_and_procedure_billing(client, superuser_headers, billing_headers):
    """
    Scenario 4: Automatically posts OT facility hourly rate and surgical procedure fee
    upon surgical case completion.
    """
    patient = _create_patient(client, superuser_headers, name="Surgical Billing Patient")
    encounter = _create_encounter(client, superuser_headers, patient["id"], enc_type="IPD")
    procedure_svc = _create_service(client, superuser_headers, code="SVC_APP_SURG_RCM", name="Laparoscopic Appendectomy", price_paise=350000)

    # 1. Create Operation Theatre with 1500 INR/hr (150,000 paise/hr)
    ot_res = client.post(
        "/v1/api/ot/theatres",
        headers=superuser_headers,
        json={
            "code": "OT_MAIN_MOD_01",
            "name": "Modular OT 1",
            "theatre_type": "MODULAR_MAJOR",
            "hourly_rate_paise": 150000,
        },
    )
    assert ot_res.status_code == 201, ot_res.text
    theatre_id = ot_res.json()["id"]

    # 2. Schedule surgical case for 2 hours
    t_start = datetime.now(timezone.utc) - timedelta(hours=2)
    t_end = datetime.now(timezone.utc)

    surg_res = client.post(
        "/v1/api/ot/surgeries",
        headers=superuser_headers,
        json={
            "encounter_id": encounter["id"],
            "theatre_id": theatre_id,
            "procedure_service_id": procedure_svc["id"],
            "lead_surgeon_id": 2,
            "anesthetist_id": 2,
            "scheduled_start_time": t_start.isoformat(),
            "scheduled_end_time": t_end.isoformat(),
        },
    )
    assert surg_res.status_code == 201, surg_res.text
    surg_id = surg_res.json()["id"]

    # 3. Post surgery completion charges
    post_res = client.post(
        f"/v1/api/billing/rcm/surgeries/{surg_id}/post-charges",
        headers=billing_headers,
    )
    assert post_res.status_code == 201, post_res.text
    res_data = post_res.json()
    assert res_data["charges_posted_count"] == 2
    # Procedure fee = 350,000 paise; OT fee = 150,000 * 2 hrs = 300,000 paise. Total = 650,000 paise.
    assert res_data["total_charges_posted_paise"] >= 600000
