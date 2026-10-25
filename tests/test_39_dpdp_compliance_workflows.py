from datetime import date, datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import sessionmaker

from backend.domains.identity.models import User
from backend.domains.patients.models import Patient
from backend.domains.privacy.models import (
    PatientConsentRecord,
    PatientDataRightsRequest,
    PrivacyDataBreachIncident,
)


@pytest.fixture
def dpdp_client(client, multi_tenant_env):
    """Client authenticated with privacy permissions on hms_apollo."""
    return client


def test_dpdp_data_rights_request_lifecycle(client, multi_tenant_env, apollo_admin_headers):
    """Scenario: Full lifecycle of a DPDP Act patient data-rights request (M-03)."""
    headers = apollo_admin_headers

    # 1. Create a patient on apollo
    ApolloSession = sessionmaker(bind=multi_tenant_env["tenant_engines"]["hms_apollo"])
    with ApolloSession() as db:
        p = Patient(
            mrn="DPDP-PAT-001",
            full_name="Priya Sharma",
            phone_number="9876543210",
            email="priya@example.com",
            date_of_birth=date(1992, 5, 10),
            gender="FEMALE",
            blood_group="B+",
            is_active=True,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        patient_id = p.id

    # 2. Submit a data rights request (Right to Erasure)
    res = client.post(
        "/api/privacy/data-rights/requests",
        headers=headers,
        json={
            "patient_id": patient_id,
            "request_type": "ERASURE",
            "details": {"reason": "Withdrawing consent for non-essential processing"},
            "sla_days": 30,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    req_id = data["id"]
    assert data["status"] == "SUBMITTED"
    assert data["request_type"] == "ERASURE"
    assert "sla_deadline" in data

    # 3. List requests
    list_res = client.get("/api/privacy/data-rights/requests", headers=headers)
    assert list_res.status_code == 200
    assert any(r["id"] == req_id for r in list_res.json())

    # 4. Filter by patient
    p_res = client.get(
        f"/api/privacy/data-rights/requests?patient_id={patient_id}", headers=headers
    )
    assert p_res.status_code == 200
    assert len(p_res.json()) == 1

    # 5. Transition to IN_REVIEW
    review_res = client.patch(
        f"/api/privacy/data-rights/requests/{req_id}/status",
        headers=headers,
        json={"status": "IN_REVIEW", "response_notes": "Assigned to Medical Records Officer"},
    )
    assert review_res.status_code == 200
    assert review_res.json()["status"] == "IN_REVIEW"

    # 6. Rejecting without a rejection_reason should fail validation (422)
    bad_reject = client.patch(
        f"/api/privacy/data-rights/requests/{req_id}/status",
        headers=headers,
        json={"status": "REJECTED"},
    )
    assert bad_reject.status_code == 422

    # 7. Rejecting with statutory medical record retention explanation succeeds
    good_reject = client.patch(
        f"/api/privacy/data-rights/requests/{req_id}/status",
        headers=headers,
        json={
            "status": "REJECTED",
            "rejection_reason": "Statutory 3-year medical record retention applies per National Medical Commission guidelines.",
            "response_notes": "Clinical summary provided; legal retention maintained.",
        },
    )
    assert good_reject.status_code == 200
    assert good_reject.json()["status"] == "REJECTED"
    assert "Statutory 3-year" in good_reject.json()["rejection_reason"]


def test_dpdp_patient_data_access_summary(client, multi_tenant_env, apollo_admin_headers):
    """Scenario: Generates compiled DPDP Act Right to Access summary dossier (M-03)."""
    headers = apollo_admin_headers
    ApolloSession = sessionmaker(bind=multi_tenant_env["tenant_engines"]["hms_apollo"])
    with ApolloSession() as db:
        admin_user = db.query(User).filter_by(email="admin@apollo.com").first()
        p = Patient(
            mrn="DPDP-PAT-002",
            full_name="Rajesh Verma",
            phone_number="9123456789",
            email="rajesh@example.com",
            date_of_birth=date(1980, 1, 1),
            gender="MALE",
            is_active=True,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        p_id = p.id

        # Add a consent record
        db.add(
            PatientConsentRecord(
                patient_id=p_id,
                consent_type="CARE",
                purpose="GENERAL_TREATMENT",
                decision="GRANTED",
                source="DIGITAL",
                recorded_by_user_id=admin_user.id,
            )
        )
        # Add a rights request
        db.add(
            PatientDataRightsRequest(
                patient_id=p_id,
                request_type="ACCESS",
                status="SUBMITTED",
                details={},
                sla_deadline=datetime.now(timezone.utc) + timedelta(days=30),
            )
        )
        db.commit()

    res = client.get(f"/api/privacy/data-rights/patients/{p_id}/summary", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["patient_id"] == p_id
    assert data["mrn"] == "DPDP-PAT-002"
    assert data["phone_masked"] == "912****6789"
    assert len(data["active_consents"]) == 1
    assert data["active_consents"][0]["purpose"] == "GENERAL_TREATMENT"
    assert len(data["data_rights_history"]) == 1
    assert data["data_rights_history"][0]["request_type"] == "ACCESS"


def test_data_breach_incident_lifecycle(client, multi_tenant_env, apollo_admin_headers):
    """Scenario: Personal data breach register and CERT-In / DPDP incident tracking (M-04)."""
    headers = apollo_admin_headers

    # 1. Report a breach incident
    res = client.post(
        "/api/privacy/breach-incidents",
        headers=headers,
        json={
            "title": "Suspected Phishing Compromise on Pharmacy Terminal",
            "description": "Endpoint detected anomalous outbound connection after invoice OCR run.",
            "severity": "HIGH",
            "breach_type": "UNAUTHORIZED_ACCESS",
            "affected_data_categories": ["procurement_invoices", "prescriptions"],
            "estimated_affected_subjects": 15,
        },
    )
    assert res.status_code == 201, res.text
    incident = res.json()
    inc_id = incident["id"]
    assert incident["incident_code"].startswith("BREACH-")
    assert incident["status"] == "REPORTED"
    assert incident["severity"] == "HIGH"

    # 2. List incidents
    list_res = client.get("/api/privacy/breach-incidents", headers=headers)
    assert list_res.status_code == 200
    assert any(i["id"] == inc_id for i in list_res.json())

    # 3. Filter by severity
    filtered = client.get("/api/privacy/breach-incidents?severity=HIGH", headers=headers)
    assert filtered.status_code == 200
    assert all(i["severity"] == "HIGH" for i in filtered.json())

    # 4. Contain and notify authority
    update_res = client.patch(
        f"/api/privacy/breach-incidents/{inc_id}",
        headers=headers,
        json={
            "status": "CONTAINED",
            "contained": True,
            "reported_to_authority": True,
            "notified_subjects_count": 15,
            "resolution_summary": "Endpoint isolated, credentials rotated, logs preserved for CERT-In submission.",
        },
    )
    assert update_res.status_code == 200, update_res.text
    updated = update_res.json()
    assert updated["status"] == "CONTAINED"
    assert updated["contained_at"] is not None
    assert updated["reported_to_authority_at"] is not None
    assert updated["notified_subjects_count"] == 15
    assert "CERT-In" in updated["resolution_summary"]


def test_dpdp_endpoints_rbac_enforcement(client, nurse_headers):
    """Scenario: Endpoints reject users lacking privacy permissions with 403 Forbidden."""
    # Nurse does not hold privacy:write
    res = client.post(
        "/api/privacy/data-rights/requests",
        headers=nurse_headers,
        json={"patient_id": 1, "request_type": "ACCESS", "details": {}},
    )
    assert res.status_code == 403

    res_breach = client.post(
        "/api/privacy/breach-incidents",
        headers=nurse_headers,
        json={
            "title": "Unauthorized Test Incident",
            "description": "Test",
            "severity": "LOW",
            "breach_type": "LOST_DEVICE",
            "affected_data_categories": ["patients"],
        },
    )
    assert res_breach.status_code == 403

