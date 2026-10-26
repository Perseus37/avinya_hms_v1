"""
Tests for Enterprise Track 4:
- ABDM Milestone M1: ABHA Profile Generation, Verification & Scan-Share Desk
- ABDM Milestone M2: HIP / HIU Consent Artefact Tracking
- ABDM Milestone M3: FHIR R4 Health Information Exchange Document Bundles
- NHCX Gateway: National Health Claims Exchange FHIR Claim Packaging & Insurer Adjudication
"""

from datetime import datetime, timedelta, timezone
import json
import re
import pytest


def _create_patient(client, headers, name="ABDM Track Patient", dob="1987-11-20T00:00:00Z"):
    res = client.post(
        "/v1/api/patients",
        headers=headers,
        json={
            "full_name": name,
            "date_of_birth": dob,
            "gender": "FEMALE",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_encounter(client, headers, patient_id):
    res = client.post(
        "/v1/api/adt/encounters",
        headers=headers,
        json={
            "patient_id": patient_id,
            "encounter_type": "IPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


# =============================================================================
# 1. ABDM Milestone M1: ABHA Generation & Scan-Share
# =============================================================================

def test_abdm_m1_generate_and_link_abha(client, superuser_headers):
    """
    Scenario 1: Creates new patient via ABHA generation, and links ABHA to existing patient.
    """
    # 1a. Generate ABHA for a new walk-in patient
    res_new = client.post(
        "/v1/api/abdm/m1/generate-abha",
        headers=superuser_headers,
        json={
            "aadhaar_or_mobile": "9876543210",
            "preferred_abha_address": "arun.kumar@abdm",
            "full_name": "Arun Kumar",
            "gender": "MALE",
            "date_of_birth": "1989-08-15T00:00:00Z",
        },
    )
    assert res_new.status_code == 201, res_new.text
    data_new = res_new.json()
    assert data_new["abha_number"].startswith("91-")
    assert re.match(r"^\d{2}-\d{4}-\d{4}-\d{4}$", data_new["abha_number"]) is not None, f"ABHA must be 14 numeric digits: {data_new['abha_number']}"
    assert data_new["abha_address"] == "arun.kumar@abdm"
    assert data_new["full_name"] == "Arun Kumar"
    assert data_new["status"] == "ABHA_LINKED"

    # 1b. Link ABHA to an existing patient
    existing_patient = _create_patient(client, superuser_headers, name="Existing Patient Linking")
    res_link = client.post(
        "/v1/api/abdm/m1/generate-abha",
        headers=superuser_headers,
        json={
            "patient_id": existing_patient["id"],
            "aadhaar_or_mobile": "9123456789",
            "preferred_abha_address": "existing.pat@abdm",
        },
    )
    assert res_link.status_code == 201, res_link.text
    data_link = res_link.json()
    assert data_link["patient_id"] == existing_patient["id"]
    assert data_link["abha_address"] == "existing.pat@abdm"


def test_abdm_m1_scan_and_share_counter_registration(client, superuser_headers):
    """
    Scenario 2: Patient scans OPD registration QR code via their ABHA app (Aarogya Setu / ABHA).
    Transfers demographic token and creates hospital OPD check-in token.
    """
    qr_payload = json.dumps({
        "abhaAddress": "deepa.sharma@abdm",
        "name": "Deepa Sharma",
        "gender": "FEMALE",
        "dob": "1994-12-05T00:00:00Z",
        "phone": "9988776655",
    })

    res = client.post(
        "/v1/api/abdm/m1/scan-share",
        headers=superuser_headers,
        json={
            "counter_id": "COUNTER-03",
            "qr_payload": qr_payload,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["token_number"].startswith("TOKEN-COUNTER-03-")
    assert body["full_name"] == "Deepa Sharma"
    assert body["abha_address"] == "deepa.sharma@abdm"
    assert body["mrn"].startswith("MRN-")


# =============================================================================
# 2. ABDM Milestone M2 & M3: Consent Management & FHIR Bundle Exchange
# =============================================================================

def test_abdm_m2_consent_and_m3_fhir_health_data_transfer(client, superuser_headers):
    """
    Scenario 3: Records ABDM M2 consent grant notification from Consent Manager,
    validates active status, and emits an ABDM FHIR R4 Document Bundle under M3.
    """
    patient = _create_patient(client, superuser_headers, name="Consent Exchange Patient")
    encounter = _create_encounter(client, superuser_headers, patient["id"])

    # Link ABHA to patient
    client.post(
        "/v1/api/abdm/m1/generate-abha",
        headers=superuser_headers,
        json={"patient_id": patient["id"], "aadhaar_or_mobile": "9811223344", "preferred_abha_address": "consent.pat@abdm"},
    )

    # 1. Record ABDM M2 Consent Notification
    now = datetime.now(timezone.utc)
    consent_id = "CONSENT-ARTEFACT-882201"

    consent_res = client.post(
        "/v1/api/abdm/m2/consent/on-notify",
        headers=superuser_headers,
        json={
            "consent_id": consent_id,
            "patient_id": patient["id"],
            "hiu_id": "APOLLO_HIU_GATEWAY",
            "hip_id": "AVINYA_HIP_01",
            "status": "GRANTED",
            "hi_types": ["Prescription", "DiagnosticReport"],
            "date_range_from": (now - timedelta(days=90)).isoformat(),
            "date_range_to": now.isoformat(),
            "expiry_time": (now + timedelta(days=30)).isoformat(),
            "signature": "SHA256withRSA:DUMMY_DIGITAL_SIGNATURE",
        },
    )
    assert consent_res.status_code == 201, consent_res.text
    artefact = consent_res.json()
    assert artefact["consent_id"] == consent_id
    assert artefact["status"] == "GRANTED"

    # Verify retrieval
    get_res = client.get(
        f"/v1/api/abdm/m2/consent/artefacts/{consent_id}",
        headers=superuser_headers,
    )
    assert get_res.status_code == 200, get_res.text

    # 2. Add clinical prescription data for patient
    med_res = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "MED_AZITH_ABDM",
            "brand_name": "Azithral 500mg",
            "generic_name": "Azithromycin",
            "form": "TABLET",
            "strength": "500mg",
        },
    )
    med_id = med_res.json()["id"]

    client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=superuser_headers,
        json={
            "encounter_id": encounter["id"],
            "items": [{"medication_id": med_id, "dosage_instruction": "1 tab OD for 5 days", "requested_quantity": 5}],
        },
    )

    # 3. ABDM M3: HIU requests health records using active consent
    xfer_res = client.post(
        "/v1/api/abdm/m3/health-information/request",
        headers=superuser_headers,
        json={
            "consent_id": consent_id,
            "hi_types": ["Prescription", "DiagnosticReport"],
        },
    )
    assert xfer_res.status_code == 200, xfer_res.text
    xfer_data = xfer_res.json()
    assert xfer_data["status"] == "DISPATCHED"
    assert xfer_data["patient_abha"] == "consent.pat@abdm"

    bundle = xfer_data["fhir_bundle"]
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "document"
    entries = bundle["entry"]
    assert len(entries) >= 2  # Composition, Patient, and MedicationRequest
    assert any(e["resource"]["resourceType"] == "Composition" for e in entries)
    assert any(e["resource"]["resourceType"] == "Patient" for e in entries)
    assert any(e["resource"]["resourceType"] == "MedicationRequest" for e in entries)


# =============================================================================
# 3. NHCX: National Health Claims Exchange Connector
# =============================================================================

def test_nhcx_claim_bundle_submission_and_adjudication_callback(client, superuser_headers, billing_headers):
    """
    Scenario 4: Compiles an internal insurance claim into a standardized NHCX FHIR R4
    Claim Bundle, submits it to the exchange, and receives digital insurer adjudication.
    """
    patient = _create_patient(client, superuser_headers, name="NHCX Insurance Patient")
    encounter = _create_encounter(client, superuser_headers, patient["id"])

    # 1. Setup Insurance Provider, Plan & Patient Policy
    prov_res = client.post(
        "/v1/api/insurance/providers",
        headers=billing_headers,
        json={
            "code": "PROV_HDFC_ERGO",
            "name": "HDFC ERGO General Insurance",
            "provider_type": "INSURER",
        },
    )
    provider_id = prov_res.json()["id"]

    plan_res = client.post(
        "/v1/api/insurance/plans",
        headers=billing_headers,
        json={
            "provider_id": provider_id,
            "code": "PLAN_HDFC_OPTIMA",
            "name": "Optima Secure Comprehensive",
            "default_copay_percentage": 0,
        },
    )
    plan_id = plan_res.json()["id"]

    now = datetime.now(timezone.utc)
    policy_res = client.post(
        "/v1/api/insurance/policies",
        headers=billing_headers,
        json={
            "patient_id": patient["id"],
            "provider_id": provider_id,
            "plan_id": plan_id,
            "policy_number": "POL-HDFC-998877",
            "member_id": "MEM-HDFC-001",
            "sum_insured_paise": 100000000,
            "copay_percentage": 0,
            "valid_from": (now - timedelta(days=30)).isoformat(),
            "valid_till": (now + timedelta(days=335)).isoformat(),
        },
    )
    policy_id = policy_res.json()["id"]

    # 2. Create Service & Invoice with a line item
    svc_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={
            "category_id": 1,
            "code": "SVC_ICU_NHCX",
            "name": "ICU Room and Monitoring Care",
            "service_type": "LAB",
            "base_price_paise": 250000,
        },
    )
    svc_id = svc_res.json()["id"]

    inv_res = client.post(
        "/v1/api/billing/invoices",
        headers=billing_headers,
        json={"patient_id": patient["id"], "encounter_id": encounter["id"], "notes": "NHCX IPD Invoice"},
    )
    invoice_id = inv_res.json()["id"]

    item_res = client.post(
        f"/v1/api/billing/invoices/{invoice_id}/items",
        headers=billing_headers,
        json={
            "service_id": svc_id,
            "description": "ICU Room and Monitoring Care (2 Days)",
            "quantity": 2,
            "unit_price_paise": 250000,
            "gst_rate_percent": 0,
        },
    )
    assert item_res.status_code == 201, item_res.text

    # Finalize invoice to UNPAID status before submitting insurance claim
    fin_res = client.patch(
        f"/v1/api/billing/invoices/{invoice_id}/finalize",
        headers=billing_headers,
    )
    assert fin_res.status_code == 200, fin_res.text

    # 3. Create Insurance Claim for 500,000 paise (5,000 INR)
    claim_res = client.post(
        "/v1/api/insurance/claims",
        headers=billing_headers,
        json={
            "invoice_id": invoice_id,
            "policy_id": policy_id,
            "claimed_amount_paise": 500000,
            "insurer_payable_paise": 500000,
            "patient_copay_paise": 0,
        },
    )
    assert claim_res.status_code == 201, claim_res.text
    claim = claim_res.json()
    claim_id = claim["id"]

    # 4. Submit to NHCX Gateway
    submit_res = client.post(
        "/v1/api/nhcx/claims/submit-bundle",
        headers=billing_headers,
        json={"claim_id": claim_id},
    )
    assert submit_res.status_code == 201, submit_res.text
    submit_data = submit_res.json()
    txn_id = submit_data["transaction_id"]
    assert txn_id.startswith("NHCX-")
    assert submit_data["nhcx_status"] == "SUBMITTED"

    bundle = submit_data["fhir_claim_bundle"]
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "collection"
    assert any(e["resource"]["resourceType"] == "Claim" for e in bundle["entry"])
    assert any(e["resource"]["resourceType"] == "Coverage" for e in bundle["entry"])

    # 5. Receive NHCX Insurer Adjudication Callback
    callback_res = client.post(
        "/v1/api/nhcx/claims/adjudication-callback",
        headers=billing_headers,
        json={
            "transaction_id": txn_id,
            "adjudication_status": "APPROVED",
            "settled_amount_paise": 480000,  # 4800 INR settled (200 INR deduction)
            "settlement_utr": "UTR-HDFC-2026-990011",
            "adjudication_bundle": {"adjudicationReason": "Adjudicated under Optima Secure standard terms"},
        },
    )
    assert callback_res.status_code == 200, callback_res.text
    callback_data = callback_res.json()
    assert callback_data["claim_status"] == "SETTLED"
    assert callback_data["settled_amount_paise"] == 480000
    assert callback_data["settlement_utr_reference"] == "UTR-HDFC-2026-990011"

    # 6. Verify Transaction Log Retrieval
    log_res = client.get(
        f"/v1/api/nhcx/transactions/{txn_id}",
        headers=billing_headers,
    )
    assert log_res.status_code == 200, log_res.text
    log_data = log_res.json()
    assert log_data["transaction_id"] == txn_id
    assert log_data["nhcx_status"] == "APPROVED"
    assert log_data["adjudicated_amount_paise"] == 480000
