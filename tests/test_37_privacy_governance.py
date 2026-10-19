from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.domains.identity.models import Permission, Role, User
from backend.domains.patients.models import Patient
from backend.domains.privacy.models import (
    PatientConsentRecord,
    PrivacyLegalHold,
    PrivacyPolicyRevision,
    PrivacyRetentionPolicy,
)


def _create_patient(db: Session) -> int:
    patient = Patient(
        mrn="PRIVACY-001",
        full_name="Privacy Test Patient",
        date_of_birth=datetime(1990, 1, 1, tzinfo=timezone.utc),
        gender="OTHER",
        is_active=True,
    )
    db.add(patient)
    db.commit()
    return patient.id


def test_consent_ledger_records_grant_withdrawal_and_regrant(
    client, multi_tenant_env, superuser_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        patient_id = _create_patient(db)

    endpoint = f"/v1/api/privacy/patients/{patient_id}/consents"
    grant = client.post(
        endpoint,
        headers=superuser_headers,
        json={
            "consent_type": "RESEARCH",
            "purpose": "Approved observational study",
            "decision": "GRANTED",
            "source": "WRITTEN",
            "evidence_reference": "CONSENT-2026-001",
        },
    )
    assert grant.status_code == 201, grant.text
    assert grant.json()["recorded_by_user_id"] > 0
    assert client.post(
        endpoint,
        headers=superuser_headers,
        json={
            "consent_type": "RESEARCH",
            "purpose": "Approved observational study",
            "decision": "GRANTED",
            "source": "DIGITAL",
        },
    ).status_code == 409

    withdrawn = client.post(
        endpoint,
        headers=superuser_headers,
        json={
            "consent_type": "RESEARCH",
            "purpose": "Approved observational study",
            "decision": "WITHDRAWN",
            "source": "VERBAL",
        },
    )
    assert withdrawn.status_code == 201, withdrawn.text
    regrant = client.post(
        endpoint,
        headers=superuser_headers,
        json={
            "consent_type": "RESEARCH",
            "purpose": "Approved observational study",
            "decision": "GRANTED",
            "source": "WRITTEN",
        },
    )
    assert regrant.status_code == 201, regrant.text
    history = client.get(endpoint, headers=superuser_headers)
    assert [item["decision"] for item in history.json()["items"]] == [
        "GRANTED",
        "WITHDRAWN",
        "GRANTED",
    ]
    with Session(engine) as db:
        assert db.query(PatientConsentRecord).count() == 3


def test_consent_withdrawal_requires_current_grant_and_authorization(
    client, multi_tenant_env, superuser_headers, doctor_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        patient_id = _create_patient(db)
    endpoint = f"/v1/api/privacy/patients/{patient_id}/consents"
    payload = {
        "consent_type": "COMMUNICATIONS",
        "purpose": "Appointment reminders",
        "decision": "WITHDRAWN",
        "source": "DIGITAL",
    }
    assert client.post(endpoint, headers=superuser_headers, json=payload).status_code == 409
    assert client.post(endpoint, headers=doctor_headers, json=payload).status_code == 403

    expired_grant = client.post(
        endpoint,
        headers=superuser_headers,
        json={
            **payload,
            "decision": "GRANTED",
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        },
    )
    assert expired_grant.status_code == 201, expired_grant.text
    with Session(engine) as db:
        record = db.get(PatientConsentRecord, expired_grant.json()["id"])
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert client.post(endpoint, headers=superuser_headers, json=payload).status_code == 409


def test_retention_policy_is_versioned_and_never_triggers_deletion(
    client, multi_tenant_env, superuser_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        _create_patient(db)
    payload = {
        "data_category": "clinical_records",
        "retention_days": 3650,
        "legal_basis": "Hospital-approved schedule pending annual review",
        "approval_reference": "POLICY-2026-01",
        "effective_from": date.today().isoformat(),
    }
    first = client.post(
        "/v1/api/privacy/retention-policies",
        headers=superuser_headers,
        json=payload,
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/v1/api/privacy/retention-policies",
        headers=superuser_headers,
        json={**payload, "retention_days": 4000, "approval_reference": "POLICY-2026-02"},
    )
    assert second.status_code == 201, second.text
    policies = client.get(
        "/v1/api/privacy/retention-policies?data_category=clinical_records",
        headers=superuser_headers,
    )
    assert [item["approval_reference"] for item in policies.json()["items"]] == [
        "POLICY-2026-02",
        "POLICY-2026-01",
    ]
    with Session(engine) as db:
        assert db.query(PrivacyRetentionPolicy).count() == 2
        assert db.query(Patient).filter(Patient.mrn == "PRIVACY-001").count() == 1


def test_retention_policy_requires_approval_reference(
    client, superuser_headers
):
    response = client.post(
        "/v1/api/privacy/retention-policies",
        headers=superuser_headers,
        json={
            "data_category": "audit_logs",
            "retention_days": 365,
            "legal_basis": "Approved retention schedule",
            "approval_reference": " ",
            "effective_from": date.today().isoformat(),
        },
    )
    assert response.status_code == 422


def test_privacy_policy_draft_requires_independent_approval_and_stays_off(
    client, multi_tenant_env, superuser_headers, doctor_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        admin_role = db.query(Role).filter_by(code="ROLE_ADMIN").one()
        approve_permission = (
            db.query(Permission).filter_by(code="privacy:approve").one()
        )
        assert approve_permission in admin_role.permissions

    payload = {
        "policy_type": "CONSENT",
        "policy_document": {
            "purposes": [{"code": "hospital-defined-purpose"}],
            "exceptions": [],
            "no_record_handling": "hospital-defined",
        },
    }
    unauthorized = client.post(
        "/v1/api/privacy/policies",
        headers=doctor_headers,
        json=payload,
    )
    assert unauthorized.status_code == 403

    created = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json=payload,
    )
    assert created.status_code == 201, created.text
    revision_id = created.json()["id"]
    assert created.json()["status"] == "DRAFT"
    assert created.json()["enforcement_mode"] == "OFF"
    assert created.json()["policy_document"] == payload["policy_document"]

    approval = client.post(
        f"/v1/api/privacy/policies/{revision_id}/approve",
        headers=superuser_headers,
        json={"approval_reference": "PRIVACY-APPROVAL-001"},
    )
    assert approval.status_code == 409

    with Session(engine) as db:
        revision = db.get(PrivacyPolicyRevision, revision_id)
        assert revision is not None
        revision.configured_by_user_id = (
            db.query(User).filter_by(email="doctor@default.com").one().id
        )
        db.commit()

    approval = client.post(
        f"/v1/api/privacy/policies/{revision_id}/approve",
        headers=superuser_headers,
        json={"approval_reference": "PRIVACY-APPROVAL-001"},
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()["status"] == "APPROVED"
    assert approval.json()["enforcement_mode"] == "OFF"
    assert approval.json()["approval_reference"] == "PRIVACY-APPROVAL-001"

    with Session(engine) as db:
        revision = db.get(PrivacyPolicyRevision, revision_id)
        revision.enforcement_mode = "ENFORCE"
        with pytest.raises(IntegrityError):
            db.commit()
            db.rollback()


def test_legal_hold_requires_scope_and_independent_release(
    client, multi_tenant_env, superuser_headers, doctor_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        patient_id = _create_patient(db)

    base_url = "/v1/api/privacy/legal-holds"
    unauthorized = client.post(
        base_url,
        headers=doctor_headers,
        json={
            "patient_id": patient_id,
            "reason": "Pending legal review",
            "hold_reference": "HOLD-UNAUTHORIZED",
        },
    )
    assert unauthorized.status_code == 403

    no_scope = client.post(
        base_url,
        headers=superuser_headers,
        json={
            "reason": "Pending legal review",
            "hold_reference": "HOLD-001",
        },
    )
    assert no_scope.status_code == 422

    created = client.post(
        base_url,
        headers=superuser_headers,
        json={
            "patient_id": patient_id,
            "reason": "Pending legal review",
            "hold_reference": "HOLD-002",
        },
    )
    assert created.status_code == 201, created.text
    hold_id = created.json()["id"]
    assert len(client.get(base_url, headers=superuser_headers).json()["items"]) == 1

    release_payload = {"release_reference": "HOLD-RELEASE-002"}
    self_release = client.post(
        f"{base_url}/{hold_id}/release",
        headers=superuser_headers,
        json=release_payload,
    )
    assert self_release.status_code == 409

    with Session(engine) as db:
        hold = db.get(PrivacyLegalHold, hold_id)
        hold.created_by_user_id = (
            db.query(User).filter_by(email="doctor@default.com").one().id
        )
        db.commit()

    released = client.post(
        f"{base_url}/{hold_id}/release",
        headers=superuser_headers,
        json=release_payload,
    )
    assert released.status_code == 200, released.text
    assert released.json()["released_by_user_id"]
    assert released.json()["release_reference"] == "HOLD-RELEASE-002"
    assert client.get(base_url, headers=superuser_headers).json()["items"] == []
