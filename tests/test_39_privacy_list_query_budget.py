"""Query-budget and equivalence tests for batched privacy evaluation (synthetic data only)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from backend.domains.identity.models import User
from backend.domains.patients.models import GenderEnum, Patient
from backend.domains.privacy.models import (
    PatientConsentRecord,
    PrivacyDecisionAudit,
    PrivacyPolicyRevision,
)
from backend.domains.privacy.runtime import (
    calculate_patient_age,
    evaluate_tenant_data_use,
    evaluate_tenant_patient_data_uses,
)

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_POINT = "patients.mpi.search"
API = "/v1/api"


def _count_statements(engine, fn):
    counter = {"statements": 0}

    def before_cursor_execute(conn, cursor, statement, params, context, many):
        counter["statements"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    return counter["statements"], result


def _add_patients(engine, count: int, prefix: str) -> list[int]:
    with Session(engine) as db:
        patients = [
            Patient(
                mrn=f"{prefix}-{index:03d}",
                full_name=f"Synthetic {prefix} {index}",
                date_of_birth=datetime(1985, 1, 1, tzinfo=timezone.utc),
                gender=GenderEnum.OTHER,
                is_active=True,
            )
            for index in range(count)
        ]
        db.add_all(patients)
        db.commit()
        return [patient.id for patient in patients]


def test_list_endpoints_use_constant_queries_without_active_policy(
    client, multi_tenant_env, doctor_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    _add_patients(engine, 40, "BUDGET")

    small, small_response = _count_statements(
        engine,
        lambda: client.get(f"{API}/patients?page_size=1", headers=doctor_headers),
    )
    large, large_response = _count_statements(
        engine,
        lambda: client.get(f"{API}/patients?page_size=40", headers=doctor_headers),
    )
    assert small_response.status_code == 200
    assert large_response.status_code == 200
    assert len(small_response.json()["items"]) == 1
    assert len(large_response.json()["items"]) == 40
    assert large <= small + 2, (small, large)

    fhir_small, fhir_small_response = _count_statements(
        engine,
        lambda: client.get(f"{API}/fhir/R4/Patient?_count=1", headers=doctor_headers),
    )
    fhir_large, fhir_large_response = _count_statements(
        engine,
        lambda: client.get(f"{API}/fhir/R4/Patient?_count=40", headers=doctor_headers),
    )
    assert fhir_small_response.status_code == 200
    assert fhir_large_response.status_code == 200
    assert len(fhir_large_response.json()["entry"]) == 40
    assert fhir_large <= fhir_small + 2, (fhir_small, fhir_large)


def _activate_synthetic_policy(db: Session) -> int:
    """Insert an approved, active audit-only revision directly (synthetic test data)."""
    admin_id = db.query(User).filter_by(email="admin@default.com").one().id
    doctor_id = db.query(User).filter_by(email="doctor@default.com").one().id
    document = json.loads(
        (FIXTURES / "privacy_policy_strict.json").read_text(encoding="utf-8")
    )
    document["purposes"].append(
        {
            "code": "QUERY_BUDGET_USE",
            "display_name": "Query budget test purpose",
            "description": "Synthetic purpose for query-budget tests.",
            "consent_required": True,
            "legal_basis": "Synthetic test only.",
            "required_before": ["use", "disclose"],
            "scope": "patient",
            "validity": "until_withdrawn",
            "capture": ["WRITTEN"],
            "consenters": ["patient"],
            "default_when_no_record": "deny",
        }
    )
    document["data_use_points"].update(
        {SEARCH_POINT: "QUERY_BUDGET_USE", "fhir.patient.search": "QUERY_BUDGET_USE"}
    )
    now = datetime.now(timezone.utc)
    revision = PrivacyPolicyRevision(
        policy_type="CONSENT",
        policy_document=document,
        status="APPROVED",
        enforcement_mode="AUDIT_ONLY",
        is_active=True,
        configured_by_user_id=admin_id,
        approved_by_user_id=doctor_id,
        approved_at=now,
        approval_reference="SYNTHETIC-QUERY-BUDGET",
        mode_set_by_user_id=doctor_id,
        mode_set_at=now,
        mode_approval_reference="SYNTHETIC-QUERY-BUDGET",
    )
    db.add(revision)
    db.commit()
    return revision.id


def _record_consent(db, *, patient_id, revision_id, decision, recorded_at, recorded_by):
    db.add(
        PatientConsentRecord(
            patient_id=patient_id,
            consent_type="SYNTHETIC",
            purpose="QUERY_BUDGET_USE",
            decision=decision,
            source="WRITTEN",
            evidence_reference=f"SYNTHETIC-{patient_id}-{decision}",
            scope_type="patient",
            consenter_type="patient",
            policy_revision_id=revision_id,
            recorded_by_user_id=recorded_by,
            recorded_at=recorded_at,
        )
    )


def test_batch_evaluation_matches_single_evaluation_with_constant_queries(
    client, multi_tenant_env, doctor_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    patient_ids = _add_patients(engine, 30, "EQUIV")

    with Session(engine) as db:
        revision_id = _activate_synthetic_policy(db)
        admin_id = db.query(User).filter_by(email="admin@default.com").one().id
        base = datetime.now(timezone.utc) - timedelta(days=1)
        for index, patient_id in enumerate(patient_ids):
            if index % 3 == 0:  # granted
                _record_consent(
                    db, patient_id=patient_id, revision_id=revision_id,
                    decision="GRANTED", recorded_at=base, recorded_by=admin_id,
                )
            elif index % 3 == 1:  # granted, then withdrawn
                _record_consent(
                    db, patient_id=patient_id, revision_id=revision_id,
                    decision="GRANTED", recorded_at=base, recorded_by=admin_id,
                )
                _record_consent(
                    db, patient_id=patient_id, revision_id=revision_id,
                    decision="WITHDRAWN", recorded_at=base + timedelta(hours=1),
                    recorded_by=admin_id,
                )
            # index % 3 == 2: no consent record
        db.commit()

    with Session(engine) as db:
        audits_before = db.execute(
            select(func.count()).select_from(PrivacyDecisionAudit)
        ).scalar_one()

        batch = evaluate_tenant_patient_data_uses(
            db, data_use_point=SEARCH_POINT, patient_ids=patient_ids, actor_user_id=None
        )
        audits_after_batch = db.execute(
            select(func.count()).select_from(PrivacyDecisionAudit)
        ).scalar_one()
        assert audits_after_batch - audits_before == len(patient_ids)

        singles = []
        for patient_id in sorted(patient_ids):
            patient = db.get(Patient, patient_id)
            singles.append(
                evaluate_tenant_data_use(
                    db,
                    data_use_point=SEARCH_POINT,
                    patient_id=patient_id,
                    actor_user_id=None,
                    patient_age=calculate_patient_age(patient.date_of_birth),
                    commit_audit=False,
                )
            )
        db.rollback()

    as_tuples = lambda items: [  # noqa: E731
        (d.allowed, d.reason_code, d.purpose_code, d.mode, d.would_deny) for d in items
    ]
    assert as_tuples(batch) == as_tuples(singles)
    assert {d.reason_code for d in batch} == {
        "valid_consent",
        "consent_withdrawn",
        "consent_missing_or_invalid",
    }
    assert all(d.allowed and d.mode == "AUDIT_ONLY" for d in batch)  # audit-only never denies
    assert sum(d.would_deny for d in batch) == 20

    def measure(ids):
        with Session(engine) as db:
            count, _ = _count_statements(
                engine,
                lambda: evaluate_tenant_patient_data_uses(
                    db, data_use_point=SEARCH_POINT, patient_ids=ids, actor_user_id=None
                ),
            )
            db.rollback()
            return count

    assert measure(patient_ids) <= measure(patient_ids[:3]) + 1

    small, small_response = _count_statements(
        engine,
        lambda: client.get(f"{API}/patients?page_size=1", headers=doctor_headers),
    )
    large, large_response = _count_statements(
        engine,
        lambda: client.get(f"{API}/patients?page_size=30", headers=doctor_headers),
    )
    assert small_response.status_code == 200
    assert large_response.status_code == 200  # audit-only never denies the caller
    assert large <= small + 3, (small, large)
