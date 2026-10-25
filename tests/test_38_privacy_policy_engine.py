import json
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.domains.privacy.data_use_points import (
    DATA_USE_POINTS,
    validate_data_use_point_registry,
)
from backend.domains.privacy.decision_engine import (
    ConsentEvidence,
    evaluate_data_use,
)
from backend.domains.privacy.policy_schema import (
    ConsentPolicyV1,
    questionnaire_answers_to_policy_document,
    validate_policy_document,
)
from backend.domains.privacy.retention_preview import (
    RetentionCategory,
    RetentionCategoryRegistry,
    preview_retention_counts,
)
from backend.core.config import Settings, settings
from backend.domains.identity.models import User
from backend.domains.patients.models import GenderEnum, Patient
from backend.domains.privacy.models import PatientConsentRecord, PrivacyDecisionAudit, PrivacyPolicyRevision, PrivacyLegalHold
from sqlalchemy.orm import Session
from backend.main import app, v1_app


FIXTURES = Path(__file__).parent / "fixtures"

NON_PATIENT_OR_DEFERRED_GET_ROUTES = {
    "/api/admin/policies",
    "/api/ambulance/vehicles",
    "/api/analytics/appointment-performance",
    "/api/analytics/care-timings",
    "/api/analytics/clinical-quality-and-safety",
    "/api/analytics/clinical-vitals-and-early-warning",
    "/api/analytics/dashboard",
    "/api/analytics/doctor-and-department-productivity",
    "/api/analytics/executive-command-center",
    "/api/analytics/financial-revenue-cycle",
    "/api/analytics/inventory-and-blood-radar",
    "/api/analytics/operational-throughput",
    "/api/analytics/patient-demographics-and-epidemiology",
    "/api/analytics/pharmacy-and-therapeutics",
    "/api/analytics/service-demand",
    "/api/analytics/trends",
    "/api/auth/departments",
    "/api/auth/me",
    "/api/auth/password/reset",
    "/api/auth/permissions",
    "/api/auth/permissions/matrix",
    "/api/auth/roles",
    "/api/auth/users",
    "/api/auth/users/{user_id}",
    "/api/blood-bank/bags",
    "/api/clinical/departments/{department_id}/taxonomy/{taxonomy_type}",
    "/api/cssd/batches",
    "/api/cssd/instrument-sets",
    "/api/diagnostics/catalog",
    "/api/diagnostics/catalog/{item_id}",
    "/api/diagnostics/catalog/{parent_id}/bundles",
    "/api/diagnostics/categories",
    "/api/diagnostics/categories/{category_id}",
    "/api/equipment/",
    "/api/equipment/logs",
    "/api/federation/clients",
    "/api/fhir/R4/metadata",
    "/api/insurance/plans",
    "/api/insurance/plans/{plan_id}/tariffs",
    "/api/insurance/providers",
    "/api/ip/beds/map-view",
    "/api/ip/floors",
    "/api/ip/wards",
    "/api/ip/wards/{ward_id}/rooms",
    "/api/ip/wards/{ward_id}/shifts",
    "/api/opd/doctors/{doctor_id}/leaves",
    "/api/ot/theatres",
    "/api/patients/pincode-suggestions",
    "/api/pharmacy/inventory/batches",
    "/api/pharmacy/medications",
    "/api/privacy/breach-incidents",
    "/api/privacy/breach-incidents/{incident_id}",
    "/api/privacy/data-rights/patients/{patient_id}/summary",
    "/api/privacy/data-rights/requests",
    "/api/privacy/data-rights/requests/{request_id}",
    "/api/privacy/data-use-points",
    "/api/privacy/decision-audits",
    "/api/privacy/legal-holds",
    "/api/privacy/patients/{patient_id}/consents",
    "/api/privacy/policies",
    "/api/privacy/retention-policies",
    "/api/privacy/retention/jobs/{job_id}",
    "/api/procurement/invoices",
    "/api/procurement/invoices/{invoice_id}",
    "/api/procurement/invoices/{invoice_id}/document",
    "/api/procurement/purchase-orders/{po_id}/print",
    "/api/procurement/vendors/{vendor_id}/metrics",
    "/api/reports/templates",
    "/api/reports/templates/variables/{report_type}",
    "/api/workforce/attendance",
    "/api/workforce/leave-requests",
    "/api/workforce/payroll-lines/{line_id}/history",
    "/api/workforce/payroll-periods",
    "/api/workforce/payroll-periods/{period_id}/lines",
    "/api/workforce/salary-profiles",
    "/health",
    "/health/ready",
}


def _policy(name: str) -> ConsentPolicyV1:
    with (FIXTURES / f"privacy_policy_{name}.json").open(encoding="utf-8") as stream:
        return validate_policy_document("CONSENT", json.load(stream))


def test_sample_policies_validate_and_produce_distinct_hospital_rules():
    strict, lenient, no_emergency, minors = (
        _policy("strict"),
        _policy("lenient"),
        _policy("no_emergency"),
        _policy("minors_only"),
    )
    decisions = [
        evaluate_data_use(
            policy,
            data_use_point="fhir.patient.read",
            mode="ENFORCE",
            policy_revision_id=1,
        )
        for policy in (strict, lenient, no_emergency, minors)
    ]
    assert [item.allowed for item in decisions] == [False, True, False, False]
    emergency = evaluate_data_use(
        strict,
        data_use_point="fhir.patient.read",
        mode="ENFORCE",
        exception_code="EMERGENCY",
        actor_permissions={"break_glass:request"},
        exception_reason="Synthetic emergency exercise",
    )
    no_emergency_result = evaluate_data_use(
        no_emergency,
        data_use_point="fhir.patient.read",
        mode="ENFORCE",
        exception_code="EMERGENCY",
        actor_permissions={"break_glass:request"},
        exception_reason="Synthetic emergency exercise",
    )
    assert emergency.allowed is True
    assert emergency.reason_code == "exception:EMERGENCY"
    assert no_emergency_result.allowed is False


def test_policy_validator_rejects_unknown_mapping_and_override_conflicts():
    policy = json.loads(
        (FIXTURES / "privacy_policy_strict.json").read_text(encoding="utf-8")
    )
    policy["data_use_points"]["unknown.point"] = "MISSING_PURPOSE"
    with pytest.raises(ValidationError, match="unknown purposes"):
        ConsentPolicyV1.model_validate(policy)

    policy = json.loads(
        (FIXTURES / "privacy_policy_strict.json").read_text(encoding="utf-8")
    )
    policy["data_use_points"]["not.registered"] = "EXTERNAL_SHARING"
    with pytest.raises(ValidationError, match="unsupported data-use points"):
        ConsentPolicyV1.model_validate(policy)


def test_questionnaire_answers_are_versioned_and_validated_before_import():
    policy = json.loads(
        (FIXTURES / "privacy_policy_lenient.json").read_text(encoding="utf-8")
    )
    answers = {key: value for key, value in policy.items() if key != "schema_version"}
    imported = questionnaire_answers_to_policy_document("CONSENT", answers)
    assert imported["schema_version"] == 1
    assert imported["policy_code"] == "lenient-synthetic"

    with pytest.raises(ValueError, match="must not provide schema_version"):
        questionnaire_answers_to_policy_document("CONSENT", policy)

    policy = json.loads(
        (FIXTURES / "privacy_policy_strict.json").read_text(encoding="utf-8")
    )
    policy["never_override"].append("EXTERNAL_SHARING")
    with pytest.raises(ValidationError, match="cannot be covered"):
        ConsentPolicyV1.model_validate(policy)


def test_latest_withdrawal_overrides_grant_and_audit_only_never_denies():
    policy = _policy("strict")
    now = datetime.now(timezone.utc)
    records = [
        ConsentEvidence(
            id=1,
            decision="GRANTED",
            source="WRITTEN",
            evidence_reference="SYNTHETIC-1",
            expires_at=None,
            recorded_at=now - timedelta(minutes=1),
            consenter_type="patient",
            scope_type="recipient",
            scope_key="partner-a",
            policy_revision_id=9,
        ),
        ConsentEvidence(
            id=2,
            decision="WITHDRAWN",
            source="WRITTEN",
            evidence_reference="SYNTHETIC-2",
            expires_at=None,
            recorded_at=now,
            consenter_type="patient",
            scope_type="recipient",
            scope_key="partner-a",
            policy_revision_id=9,
        ),
    ]
    enforced = evaluate_data_use(
        policy,
        data_use_point="fhir.patient.read",
        mode="ENFORCE",
        consents=records,
        policy_revision_id=9,
        recipient_or_scope_key="partner-a",
        now=now,
    )
    audited = evaluate_data_use(
        policy,
        data_use_point="fhir.patient.read",
        mode="AUDIT_ONLY",
        consents=records,
        policy_revision_id=9,
        recipient_or_scope_key="partner-a",
        now=now,
    )
    assert enforced.allowed is False
    assert enforced.reason_code == "consent_withdrawn"
    assert audited.allowed is True
    assert audited.would_deny is True

    allow_by_default = policy.model_copy(deep=True)
    allow_by_default.purposes[0].default_when_no_record = "allow"
    withdrawn = evaluate_data_use(
        allow_by_default,
        data_use_point="fhir.patient.read",
        mode="ENFORCE",
        consents=records,
        policy_revision_id=9,
        recipient_or_scope_key="partner-a",
        now=now,
    )
    assert withdrawn.allowed is False
    assert withdrawn.reason_code == "consent_withdrawn"


def test_consent_scope_capture_and_evidence_are_part_of_decision():
    policy = _policy("strict")
    now = datetime.now(timezone.utc)

    def evidence(source: str, scope_key: str, evidence_reference: str | None):
        return ConsentEvidence(
            id=1,
            decision="GRANTED",
            source=source,
            evidence_reference=evidence_reference,
            expires_at=None,
            recorded_at=now,
            consenter_type="patient",
            scope_type="recipient",
            scope_key=scope_key,
            policy_revision_id=4,
        )

    def evaluate(record):
        return evaluate_data_use(
            policy,
            data_use_point="fhir.patient.read",
            mode="ENFORCE",
            consents=[record],
            policy_revision_id=4,
            recipient_or_scope_key="partner-a",
            now=now,
        )

    assert evaluate(evidence("WRITTEN", "partner-a", "EVIDENCE")).allowed
    assert not evaluate(evidence("DIGITAL", "partner-a", "EVIDENCE")).allowed
    assert not evaluate(evidence("WRITTEN", "partner-b", "EVIDENCE")).allowed
    assert not evaluate(evidence("WRITTEN", "partner-a", None)).allowed


def test_configured_minor_group_requires_guardian_consent():
    policy = _policy("minors_only")
    now = datetime.now(timezone.utc)

    def consent(consenter_type: str) -> ConsentEvidence:
        return ConsentEvidence(
            id=1,
            decision="GRANTED",
            source="GUARDIAN_SIGNATURE",
            evidence_reference="SYNTHETIC-GUARDIAN",
            expires_at=None,
            recorded_at=now,
            consenter_type=consenter_type,
            scope_type="patient",
            scope_key=None,
            policy_revision_id=8,
        )

    kwargs = {
        "policy": policy,
        "data_use_point": "fhir.patient.read",
        "mode": "ENFORCE",
        "policy_revision_id": 8,
        "patient_age": 15,
    }
    patient_record = evaluate_data_use(**kwargs, consents=[consent("patient")])
    guardian_record = evaluate_data_use(**kwargs, consents=[consent("guardian")])
    assert patient_record.allowed is False
    assert guardian_record.allowed is True


def test_route_registry_coverage_and_unknown_policy_points_fail_validation():
    route_points = set()
    for target in (app, v1_app):
        for operations in target.openapi()["paths"].values():
            for operation in operations.values():
                if not isinstance(operation, dict):
                    continue
                point = operation.get("x-privacy-data-use-point")
                if point:
                    assert point in DATA_USE_POINTS
                    route_points.add(point)
    assert route_points == set(DATA_USE_POINTS)
    assert {
        code
        for code, point in DATA_USE_POINTS.items()
        if point.action == "use"
    } == {
        "patients.mpi.search",
        "patients.mpi.read",
        "patients.allergies.read",
        "clinical.encounter.vitals.read",
        "clinical.encounter.notes.read",
        "clinical.encounter.diagnoses.read",
        "clinical.patient.comorbidities.read",
        "clinical.encounter.physical_exams.read",
        "adt.encounters.active.read",
        "adt.encounters.read",
        "adt.admissions.search",
        "adt.admissions.read",
        "adt.admission.transfers.read",
        "adt.discharge_summary.print",
        "nursing.emar.read",
        "nursing.fluid_balance.read",
        "er.triage_queue.read",
        "registry.births.read",
        "registry.births.print",
        "registry.deaths.print",
        "registry.mlc.print",
        "opd.appointments.search",
        "opd.appointments.read",
        "opd.appointment.print",
        "opd.appointment.history.read",
        "opd.waitlist.search",
        "opd.waitlist.history.read",
        "opd.service_demand.search",
        "opd.service_demand.history.read",
        "billing.invoices.search",
        "billing.invoices.read",
        "billing.payments.read",
        "billing.deposits.read",
        "billing.refunds.read",
        "billing.invoices.print",
        "billing.invoices.pdf",
        "pharmacy.prescriptions.search",
        "pharmacy.prescriptions.read",
        "pharmacy.prescription.print",
        "diagnostics.orders.search",
        "diagnostics.orders.read",
        "diagnostics.order.tasks.read",
        "diagnostics.tasks.pending.read",
        "diagnostics.tasks.panic.read",
        "diagnostics.study.read",
        "diagnostics.orders.print",
        "insurance.patient.policies.read",
        "insurance.preauth.pdf",
        "insurance.claim.pdf",
        "ambulance.dispatches.read",
        "ambulance.dispatch.history.read",
        "dietary.orders.read",
        "dietary.order.history.read",
        "mortuary.cases.read",
        "mortuary.case.history.read",
        "break_glass.patient.read",
        "break_glass.reviews.read",
        "ot.surgery.print",
    }
    openapi_paths = app.openapi()["paths"]
    for code, point in DATA_USE_POINTS.items():
        method, path = point.route.split(" ", maxsplit=1)
        operation = openapi_paths[path][method.lower()]
        assert operation["x-privacy-data-use-point"] == code
    assert validate_data_use_point_registry(
        {"fhir.patient.read": "EXTERNAL_SHARING", "not.registered": "X"}
    ) == ["not.registered"]


def test_every_api_get_route_is_registered_or_explicitly_deferred():
    unclassified_routes = set()
    for path, operations in app.openapi()["paths"].items():
        operation = operations.get("get")
        if operation is None:
            continue
        point = operation.get("x-privacy-data-use-point")
        if point is not None:
            assert point in DATA_USE_POINTS
        else:
            unclassified_routes.add(path)
    assert unclassified_routes == NON_PATIENT_OR_DEFERRED_GET_ROUTES


def test_patient_index_read_routes_are_classified_or_explicitly_excluded():
    public_location_lookup = "GET /api/patients/pincode-suggestions"
    classified_routes = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/patients") or "get" not in operations:
            continue
        operation = operations["get"]
        route = f"GET {path}"
        point = operation.get("x-privacy-data-use-point")
        if route == public_location_lookup:
            assert point is None
            continue
        assert point in DATA_USE_POINTS, f"Unclassified patient-data route: {route}"
        classified_routes.add(route)

    assert classified_routes == {
        "GET /api/patients",
        "GET /api/patients/{patient_id}",
        "GET /api/patients/{patient_id}/allergies",
    }


def test_clinical_patient_record_routes_are_classified_or_explicitly_excluded():
    public_taxonomy = (
        "GET /api/clinical/departments/{department_id}/taxonomy/{taxonomy_type}"
    )
    classified_routes = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/clinical") or "get" not in operations:
            continue
        route = f"GET {path}"
        operation = operations["get"]
        point = operation.get("x-privacy-data-use-point")
        if route == public_taxonomy:
            assert point is None
            continue
        assert point in DATA_USE_POINTS, f"Unclassified clinical route: {route}"
        classified_routes.add(route)

    assert classified_routes == {
        "GET /api/clinical/encounters/{encounter_id}/vitals",
        "GET /api/clinical/encounters/{encounter_id}/notes",
        "GET /api/clinical/encounters/{encounter_id}/diagnoses",
        "GET /api/clinical/patients/{patient_id}/comorbidities",
        "GET /api/clinical/encounters/{encounter_id}/physical-exams",
    }


def test_adt_nursing_er_and_registry_reads_have_privacy_points():
    prefixes = ("/api/adt", "/api/nursing", "/api/er", "/api/registries")
    checked_routes = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(prefixes) or "get" not in operations:
            continue
        point = operations["get"].get("x-privacy-data-use-point")
        assert point in DATA_USE_POINTS, f"Unclassified patient-data route: GET {path}"
        checked_routes.add(path)

    assert len(checked_routes) >= 12


def test_opd_patient_reads_are_classified_except_doctor_leave_schedule():
    doctor_leave_list = "GET /api/opd/doctors/{doctor_id}/leaves"
    checked_routes = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/opd") or "get" not in operations:
            continue
        route = f"GET {path}"
        point = operations["get"].get("x-privacy-data-use-point")
        if route == doctor_leave_list:
            assert point is None
            continue
        assert point in DATA_USE_POINTS, f"Unclassified OPD route: {route}"
        checked_routes.add(route)

    assert len(checked_routes) >= 7


def test_billing_pharmacy_diagnostics_and_insurance_reads_are_classified():
    non_patient_routes = {
        "GET /api/pharmacy/medications",
        "GET /api/pharmacy/inventory/batches",
        "GET /api/diagnostics/categories",
        "GET /api/diagnostics/categories/{category_id}",
        "GET /api/diagnostics/catalog",
        "GET /api/diagnostics/catalog/{item_id}",
        "GET /api/diagnostics/catalog/{parent_id}/bundles",
        "GET /api/insurance/plans",
        "GET /api/insurance/plans/{plan_id}/tariffs",
        "GET /api/insurance/providers",
    }
    prefixes = (
        "/api/billing",
        "/api/pharmacy",
        "/api/diagnostics",
        "/api/insurance",
    )
    checked_routes = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(prefixes) or "get" not in operations:
            continue
        route = f"GET {path}"
        point = operations["get"].get("x-privacy-data-use-point")
        if route in non_patient_routes:
            assert point is None
            continue
        assert point in DATA_USE_POINTS, f"Unclassified clinical/finance route: {route}"
        checked_routes.add(route)

    assert len(checked_routes) >= 20


def test_service_operations_and_emergency_patient_reads_are_classified():
    non_patient_routes = {
        "GET /api/ambulance/vehicles",
        "GET /api/ot/theatres",
    }
    prefixes = (
        "/api/ambulance",
        "/api/break-glass",
        "/api/dietary",
        "/api/mortuary",
        "/api/ot",
    )
    checked_routes = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(prefixes) or "get" not in operations:
            continue
        route = f"GET {path}"
        point = operations["get"].get("x-privacy-data-use-point")
        if route in non_patient_routes:
            assert point is None
            continue
        assert point in DATA_USE_POINTS, f"Unclassified service route: {route}"
        checked_routes.add(route)

    assert len(checked_routes) >= 9


def test_retention_preview_returns_only_aggregate_counts():
    registry = RetentionCategoryRegistry()
    registry.register(
        RetentionCategory(
            code="synthetic-attachments",
            count_candidates=lambda: 8,
            count_on_hold=lambda: 2,
        )
    )
    report = preview_retention_counts(registry, ["synthetic-attachments"])
    assert report == [
        {
            "category_code": "synthetic-attachments",
            "candidate_count": 8,
            "held_count": 2,
            "eligible_count": 6,
        }
    ]
    assert "patient_id" not in report[0]
    with pytest.raises(ValueError, match="not in the reviewed inventory"):
        preview_retention_counts(registry, ["clinical-records"])


def test_audit_only_activation_is_guarded_and_fhir_remains_allowed(
    client, multi_tenant_env, superuser_headers, monkeypatch
):
    monkeypatch.setattr(settings, "PRIVACY_ALLOW_AUDIT_ONLY", False)
    disabled = client.post(
        "/v1/api/privacy/policies/999/activate-audit-only",
        headers=superuser_headers,
        json={"approval_reference": "SYNTHETIC-DISABLED"},
    )
    assert disabled.status_code == 409
    monkeypatch.setattr(settings, "PRIVACY_ALLOW_AUDIT_ONLY", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    production_block = client.post(
        "/v1/api/privacy/policies/999/activate-audit-only",
        headers=superuser_headers,
        json={"approval_reference": "SYNTHETIC-PRODUCTION-BLOCK"},
    )
    assert production_block.status_code == 409
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        patient = Patient(
            mrn="PRIVACY-AUDIT-001",
            full_name="Synthetic Privacy Patient",
            date_of_birth=datetime(1990, 1, 1, tzinfo=timezone.utc),
            gender=GenderEnum.OTHER,
            is_active=True,
        )
        db.add(patient)
        db.commit()
        patient_id = patient.id

    policy_doc = json.loads(
        (FIXTURES / "privacy_policy_strict.json").read_text(encoding="utf-8")
    )
    policy_doc["purposes"].append(
        {
            "code": "INTERNAL_USE",
            "display_name": "Internal use",
            "description": "Synthetic route-coverage test purpose.",
            "consent_required": True,
            "legal_basis": "Synthetic test only.",
            "required_before": ["use"],
            "scope": "patient",
            "validity": "until_withdrawn",
            "capture": ["WRITTEN"],
            "consenters": ["patient"],
            "default_when_no_record": "deny",
        }
    )
    policy_doc["data_use_points"].update(
        {
            "patients.mpi.search": "INTERNAL_USE",
            "patients.mpi.read": "INTERNAL_USE",
            "patients.allergies.read": "INTERNAL_USE",
            "clinical.patient.comorbidities.read": "INTERNAL_USE",
        }
    )
    policy_doc["allow_care_flow_consent_gating"] = True
    created = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json={"policy_type": "CONSENT", "policy_document": policy_doc},
    )
    assert created.status_code == 201, created.text
    revision_id = created.json()["id"]
    with Session(engine) as db:
        revision = db.get(PrivacyPolicyRevision, revision_id)
        revision.configured_by_user_id = (
            db.query(User).filter_by(email="doctor@default.com").one().id
        )
        db.commit()

    approval = client.post(
        f"/v1/api/privacy/policies/{revision_id}/approve",
        headers=superuser_headers,
        json={"approval_reference": "SYNTHETIC-APPROVAL"},
    )
    assert approval.status_code == 200, approval.text
    activated = client.post(
        f"/v1/api/privacy/policies/{revision_id}/activate-audit-only",
        headers=superuser_headers,
        json={"approval_reference": "SYNTHETIC-AUDIT-ONLY"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["enforcement_mode"] == "AUDIT_ONLY"

    consent_url = f"/v1/api/privacy/patients/{patient_id}/consents"
    disallowed_capture = client.post(
        consent_url,
        headers=superuser_headers,
        json={
            "consent_type": "DATA_SHARING",
            "purpose": "EXTERNAL_SHARING",
            "decision": "GRANTED",
            "source": "DIGITAL",
            "scope_type": "recipient",
            "scope_key": "partner-a",
            "evidence_reference": "SYNTHETIC-EVIDENCE",
        },
    )
    assert disallowed_capture.status_code == 422
    consent = client.post(
        consent_url,
        headers=superuser_headers,
        json={
            "consent_type": "DATA_SHARING",
            "purpose": "EXTERNAL_SHARING",
            "decision": "GRANTED",
            "source": "WRITTEN",
            "scope_type": "recipient",
            "scope_key": "partner-a",
            "evidence_reference": "SYNTHETIC-EVIDENCE",
        },
    )
    assert consent.status_code == 201, consent.text
    assert consent.json()["policy_revision_id"] == revision_id
    assert consent.json()["scope_key"] == "partner-a"

    response = client.get(
        f"/v1/api/fhir/R4/Patient/{patient_id}",
        headers=superuser_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["resourceType"] == "Patient"
    patient_list = client.get("/v1/api/patients", headers=superuser_headers)
    patient_read = client.get(
        f"/v1/api/patients/{patient_id}", headers=superuser_headers
    )
    patient_allergies = client.get(
        f"/v1/api/patients/{patient_id}/allergies",
        headers=superuser_headers,
    )
    patient_comorbidities = client.get(
        f"/v1/api/clinical/patients/{patient_id}/comorbidities",
        headers=superuser_headers,
    )
    assert patient_list.status_code == 200, patient_list.text
    assert patient_read.status_code == 200, patient_read.text
    assert patient_allergies.status_code == 200, patient_allergies.text
    assert patient_comorbidities.status_code == 200, patient_comorbidities.text

    with Session(engine) as db:
        audit = db.query(PrivacyDecisionAudit).filter_by(
            data_use_point="fhir.patient.read"
        ).one()
        assert audit.result == "WOULD_DENY"
        assert audit.reason_code == "consent_missing_or_invalid"
        report = client.get(
            "/v1/api/privacy/decision-audits",
            headers=superuser_headers,
        )
        assert report.status_code == 200
        report_items = {
            item["data_use_point"]: item for item in report.json()["items"]
        }
        assert set(report_items) == {
            "fhir.patient.read",
            "patients.mpi.search",
            "patients.mpi.read",
            "patients.allergies.read",
            "clinical.patient.comorbidities.read",
        }
        assert report_items["fhir.patient.read"]["result"] == "WOULD_DENY"
        assert report_items["fhir.patient.read"]["reason_code"] == (
            "consent_missing_or_invalid"
        )
        for point in set(report_items) - {"fhir.patient.read"}:
            assert report_items[point]["result"] == "WOULD_DENY"
            assert report_items[point]["reason_code"] == "consent_missing_or_invalid"
        assert "patient_id" not in report.text


def test_settings_reject_audit_only_in_production():
        values = settings.model_dump()
        values["ENVIRONMENT"] = "production"
        values["PRIVACY_ALLOW_AUDIT_ONLY"] = True
        values["PRIVACY_ALLOW_RETENTION_DRY_RUN"] = True
        with pytest.raises(ValidationError, match="disabled in production"):
            Settings.model_validate(values)


def test_retention_dry_run_requires_approved_policy_and_returns_counts_only(
    client, multi_tenant_env, superuser_headers, monkeypatch
):
    monkeypatch.setattr(settings, "PRIVACY_ALLOW_RETENTION_DRY_RUN", True)
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    created = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json={
            "policy_type": "RETENTION",
            "policy_document": json.loads(
                (FIXTURES / "privacy_retention_policy_synthetic.json").read_text(
                    encoding="utf-8"
                )
            ),
        },
    )
    assert created.status_code == 201, created.text
    revision_id = created.json()["id"]
    with Session(engine) as db:
        revision = db.get(PrivacyPolicyRevision, revision_id)
        revision.configured_by_user_id = (
            db.query(User).filter_by(email="doctor@default.com").one().id
        )
        db.commit()
    approval = client.post(
        f"/v1/api/privacy/policies/{revision_id}/approve",
        headers=superuser_headers,
        json={"approval_reference": "SYNTHETIC-RETENTION-APPROVAL"},
    )
    assert approval.status_code == 200, approval.text

    url = "/v1/api/privacy/retention/dry-run"
    payload = {
        "policy_revision_id": revision_id,
        "categories": ["synthetic-attachments"],
    }
    not_inventoried = client.post(url, headers=superuser_headers, json=payload)
    assert not_inventoried.status_code == 409

    from backend.domains.privacy.retention_preview import (
        RetentionCategory,
        RetentionCategoryRegistry,
    )
    privacy_router_module = importlib.import_module(
        "backend.domains.privacy.router"
    )

    registry = RetentionCategoryRegistry()
    registry.register(
        RetentionCategory(
            code="synthetic-attachments",
            count_candidates=lambda: 6,
            count_on_hold=lambda: 1,
        )
    )
    monkeypatch.setattr(
        privacy_router_module, "retention_category_registry", registry
    )
    report = client.post(url, headers=superuser_headers, json=payload)
    assert report.status_code == 200, report.text
    assert report.json()["items"] == [
        {
            "category_code": "synthetic-attachments",
            "candidate_count": 6,
            "held_count": 1,
            "eligible_count": 5,
        }
    ]
    assert report.json()["destructive_action_performed"] is False
    assert "patient_id" not in report.text


def test_care_flow_points_cannot_be_mapped_to_consent_required_without_acknowledgement():
    base = {
        "schema_version": 1,
        "policy_code": "care-flow-guard-test",
        "purposes": [
            {
                "code": "EXTERNAL_SHARING",
                "display_name": "External Sharing",
                "consent_required": True,
                "legal_basis": "Consent required test purpose.",
                "required_before": ["disclose"],
                "scope": "patient",
                "validity": "until_withdrawn",
                "capture": ["WRITTEN"],
                "consenters": ["patient"],
                "default_when_no_record": "deny",
            }
        ],
        "data_use_points": {
            "clinical.encounter.notes.read": "EXTERNAL_SHARING"
        },
        "exceptions": [],
        "never_override": [],
        "groups": [],
        "withdrawal": {
            "effective": "immediate",
            "cancel_queued": True,
            "cancel_in_flight": True,
            "notify_prior_recipients": "never",
            "statutory_retention_continues": True,
            "patient_notice": "Notice text.",
            "clinician_alert_purposes": [],
            "does_not_affect_treatment": True,
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        validate_policy_document("CONSENT", base)
    assert "care-flow / treatment data-use points cannot be mapped" in str(exc_info.value)

    # When explicit acknowledgement is set, it passes validation
    base["allow_care_flow_consent_gating"] = True
    validated = validate_policy_document("CONSENT", base)
    assert validated.allow_care_flow_consent_gating is True


def test_policy_coverage_check_and_enforcement_validation():
    base = {
        "schema_version": 1,
        "policy_code": "coverage-test",
        "purposes": [
            {
                "code": "STANDARD_TREATMENT",
                "display_name": "Standard Treatment",
                "consent_required": False,
                "legal_basis": "Direct clinical care.",
            }
        ],
        "data_use_points": {
            "fhir.patient.read": "STANDARD_TREATMENT"
        },
        "exceptions": [],
        "never_override": [],
        "groups": [],
        "withdrawal": {
            "effective": "immediate",
            "cancel_queued": True,
            "cancel_in_flight": True,
            "notify_prior_recipients": "never",
            "statutory_retention_continues": True,
            "patient_notice": "Notice text.",
            "clinician_alert_purposes": [],
            "does_not_affect_treatment": True,
        },
    }
    policy = validate_policy_document("CONSENT", base)
    coverage = policy.check_coverage()
    assert "fhir.patient.read" in coverage["mapped"]
    assert len(coverage["unmapped"]) > 0

    with pytest.raises(ValueError) as exc:
        policy.validate_for_enforcement()
    assert "Policy cannot be enforced with unmapped data-use points" in str(exc.value)

    # Mark remaining points as ungated
    base["ungated_points"] = coverage["unmapped"]
    policy_full = validate_policy_document("CONSENT", base)
    policy_full.validate_for_enforcement()
    assert len(policy_full.check_coverage()["unmapped"]) == 0


def test_list_patients_filters_denied_rows_instead_of_failing_page(
    client, multi_tenant_env, superuser_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        patients = []
        for i in range(10):
            p = Patient(
                mrn=f"FILTER-MPI-{i:03d}",
                full_name=f"Patient Filter {i}",
                date_of_birth=datetime(1990, 1, 1 + i, tzinfo=timezone.utc),
                gender=GenderEnum.FEMALE if i % 2 == 0 else GenderEnum.MALE,
                phone_number=f"+919876543{i:03d}",
                is_active=True,
            )
            db.add(p)
            patients.append(p)
        db.commit()
        for p in patients:
            db.refresh(p)
        denied_patient_id = patients[0].id

        policy_doc = {
            "schema_version": 1,
            "policy_code": "row-filtering-mpi",
            "purposes": [
                {
                    "code": "MPI_PURPOSE",
                    "display_name": "MPI Access",
                    "consent_required": True,
                    "legal_basis": "Testing row filtering.",
                    "required_before": ["use"],
                    "scope": "patient",
                    "validity": "until_withdrawn",
                    "capture": ["WRITTEN"],
                    "consenters": ["patient"],
                    "default_when_no_record": "allow",
                }
            ],
            "data_use_points": {
                "patients.mpi.search": "MPI_PURPOSE",
            },
            "exceptions": [],
            "never_override": [],
            "groups": [],
            "withdrawal": {
                "effective": "immediate",
                "cancel_queued": True,
                "cancel_in_flight": True,
                "notify_prior_recipients": "never",
                "statutory_retention_continues": True,
                "patient_notice": "Notice text.",
                "clinician_alert_purposes": [],
                "does_not_affect_treatment": True,
            },
        }
        res = client.post(
            "/v1/api/privacy/policies",
            headers=superuser_headers,
            json={"policy_type": "CONSENT", "policy_document": policy_doc},
        )
        assert res.status_code == 201
        rev_id = res.json()["id"]

        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        rev = db.get(PrivacyPolicyRevision, rev_id)
        rev.configured_by_user_id = doctor.id
        db.commit()

        appr = client.post(
            f"/v1/api/privacy/policies/{rev_id}/approve",
            headers=superuser_headers,
            json={"approval_reference": "FILTER-APPROVE-REF"},
        )
        assert appr.status_code == 200

        # Activate in AUDIT_ONLY mode, and set enforcement_mode = 'ENFORCE' directly in DB for testing
        rev = db.get(PrivacyPolicyRevision, rev_id)
        rev.status = 'APPROVED'
        rev.mode_set_by_user_id = doctor.id
        rev.mode_set_at = datetime.now(timezone.utc)
        rev.mode_approval_reference = 'FILTER-APP-001'
        rev.is_active = True
        rev.enforcement_mode = "ENFORCE"
        # Add a DECLINED consent record for the first patient
        db.add(
            PatientConsentRecord(
                policy_revision_id=rev_id,
                patient_id=denied_patient_id,
                recorded_by_user_id=doctor.id,
                consent_type="CARE",
                purpose="MPI_PURPOSE",
                decision="DECLINED",
                source="WRITTEN",
                recorded_at=datetime.now(timezone.utc),
                scope_type="patient",
                consenter_type="patient",
            )
        )
        db.commit()

    # Now call list_patients
    res = client.get("/v1/api/patients?search=Filter", headers=superuser_headers)
    assert res.status_code == 200
    data = res.json()
    items = data["items"]
    returned_ids = [item["id"] for item in items]
    assert denied_patient_id not in returned_ids
    assert len(items) == 9


def test_enforce_mode_without_policy_fails_closed():
    # F-08: Under enforcement mode, missing policy must fail closed (allowed=False)
    decision = evaluate_data_use(
        None,
        data_use_point="fhir.patient.read",
        mode="ENFORCE",
    )
    assert decision.allowed is False
    assert decision.reason_code == "enforcement_policy_missing_or_invalid"


def test_expired_consent_is_explicitly_denied():
    # F-09: Expired consent record must be explicitly denied (consent_expired)
    strict = _policy("strict")
    past = datetime.now(timezone.utc) - timedelta(days=1)
    expired_evidence = [
        ConsentEvidence(
            id=1,
            decision="GRANTED",
            source="WRITTEN",
            evidence_reference="DOC-REF-001",
            expires_at=past,
            recorded_at=past - timedelta(days=10),
            scope_type="recipient",
            scope_key=None,
            policy_revision_id=1,
            consenter_type="patient",
        )
    ]
    decision = evaluate_data_use(
        strict,
        data_use_point="fhir.patient.read",
        mode="ENFORCE",
        consents=expired_evidence,
        policy_revision_id=1,
    )
    assert decision.allowed is False
    assert decision.reason_code == "consent_expired"


def test_approved_policy_document_is_immutable(multi_tenant_env):
    # F-17: Policy document cannot be edited once APPROVED
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        admin = db.query(User).filter_by(email="admin@default.com").one()
        doc = {
            "schema_version": 1,
            "policy_code": "immutability-test",
            "purposes": [
                {
                    "code": "TEST_P",
                    "display_name": "Test P",
                    "consent_required": False,
                    "legal_basis": "Basis",
                }
            ],
            "data_use_points": {},
            "exceptions": [],
            "never_override": [],
            "groups": [],
            "withdrawal": {
                "effective": "immediate",
                "cancel_queued": True,
                "cancel_in_flight": True,
                "notify_prior_recipients": "never",
                "statutory_retention_continues": True,
                "patient_notice": "Notice",
                "clinician_alert_purposes": [],
                "does_not_affect_treatment": True,
            },
        }
        rev = PrivacyPolicyRevision(
            policy_type="CONSENT",
            policy_document=doc,
            status="APPROVED",
            configured_by_user_id=doctor.id,
            approved_by_user_id=admin.id,
            approved_at=datetime.now(timezone.utc),
            approval_reference="IMMUTABLE-APP-001",
        )
        db.add(rev)
        db.commit()

        # Attempt to modify the approved policy document
        rev.policy_document = {"schema_version": 1, "policy_code": "tampered"}
        with pytest.raises(ValueError, match="Approved privacy policy documents are immutable"):
            db.commit()
        db.rollback()


def test_retention_execution_deletes_and_anonymizes_honoring_legal_holds(
    client, multi_tenant_env, superuser_headers, monkeypatch
):
    monkeypatch.setattr(settings, 'PRIVACY_ALLOW_RETENTION_EXECUTE', True)
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'testing')
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    now = datetime.now(timezone.utc)
    old_date = now - timedelta(days=400)

    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()

        # 1. Setup inactive patients: one normal, one with active legal hold
        p_normal = Patient(
            mrn="RET-NORM-01",
            full_name="Normal Inactive Patient",
            gender=GenderEnum.FEMALE,
            phone_number="+919876543999",
            date_of_birth=old_date,
            is_active=False,
            updated_at=old_date,
        )
        p_held = Patient(
            mrn="RET-HELD-01",
            full_name="Held Inactive Patient",
            gender=GenderEnum.FEMALE,
            phone_number="+919876543888",
            date_of_birth=old_date,
            is_active=False,
            updated_at=old_date,
        )
        db.add_all([p_normal, p_held])
        db.flush()

        # Add legal hold on p_held
        db.add(
            PrivacyLegalHold(
                patient_id=p_held.id,
                data_category="patients",
                reason="Pending litigation hold",
                hold_reference="HOLD-LIT-001",
                created_by_user_id=doctor.id,
            )
        )

        # 2. Setup privacy decision audits: old allow (should be deleted), old deny (kept or handled)
        db.add(
            PrivacyDecisionAudit(
                actor_user_id=doctor.id,
                data_use_point="patients.mpi.search",
                purpose_code="CARE",
                mode="AUDIT_ONLY",
                result="ALLOW",
                reason_code="routine_read",
                occurred_at=old_date,
            )
        )
        db.commit()
        p_norm_id = p_normal.id
        p_held_id = p_held.id

    # Create & approve retention policy
    retention_doc = {
        "schema_version": 1,
        "policy_code": "retention-exec-test",
        "categories": [
            {
                "code": "patients",
                "system_of_record": "Tenant Database",
                "locations": ["patients"],
                "retention_days": 365,
                "clock_start": "last_activity",
                "action": "anonymise",
                "legal_hold_triggers": ["court_order"],
                "backup_treatment": "Retain backup snapshots.",
                "export_treatment": "Exclude from exports.",
            },
            {
                "code": "privacy-decision-audits",
                "system_of_record": "Audit Ledger",
                "locations": ["privacy_decision_audits"],
                "retention_days": 90,
                "clock_start": "creation",
                "action": "delete",
                "legal_hold_triggers": [],
                "backup_treatment": "Purged from backups upon cycle.",
                "export_treatment": "Exclude.",
            },
        ],
    }
    create_res = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json={"policy_type": "RETENTION", "policy_document": retention_doc},
    )
    assert create_res.status_code == 201
    rev_id = create_res.json()["id"]

    with Session(engine) as db:
        rev = db.get(PrivacyPolicyRevision, rev_id)
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        rev.configured_by_user_id = doctor.id
        db.commit()

    appr_res = client.post(
        f"/v1/api/privacy/policies/{rev_id}/approve",
        headers=superuser_headers,
        json={"approval_reference": "RET-EXEC-APPROVAL"},
    )
    assert appr_res.status_code == 200

    # Execute retention run
    exec_payload = {
        "policy_revision_id": rev_id,
        "categories": ["patients", "privacy-decision-audits"],
        "approval_reference": "EXEC-RUN-001",
        "dry_run": False,
        "confirm_destructive_execution": True,
    }
    exec_res = client.post(
        "/v1/api/privacy/retention/execute",
        headers=superuser_headers,
        json=exec_payload,
    )
    assert exec_res.status_code == 200, exec_res.text
    exec_data = exec_res.json()
    assert exec_data["total_affected"] >= 2  # p_normal anonymized + audit deleted
    assert exec_data["total_held"] >= 1      # p_held protected

    # Verify database state after retention execution
    with Session(engine) as db:
        p_norm_after = db.get(Patient, p_norm_id)
        assert p_norm_after.full_name == "ANONYMIZED"
        assert p_norm_after.mrn.startswith("ANON-")
        assert p_norm_after.phone_number == "0000000000"
        assert p_norm_after.emergency_contact_name is None
        assert p_norm_after.emergency_contact_phone is None
        assert p_norm_after.blood_group is None
        assert p_norm_after.allow_network_sharing is False
        assert p_norm_after.date_of_birth.month == 1
        assert p_norm_after.date_of_birth.day == 1

        p_held_after = db.get(Patient, p_held_id)
        assert p_held_after.full_name == "Held Inactive Patient"  # UNTOUCHED due to legal hold!


def test_consent_carry_over_on_policy_activation(
    client, multi_tenant_env, superuser_headers, monkeypatch
):
    monkeypatch.setattr(settings, "PRIVACY_ALLOW_AUDIT_ONLY", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    # F-05: Activating v2 with carry_over_from_revision_id copies valid grants to v2
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    now = datetime.now(timezone.utc)

    # 1. Create a patient and revision 1 with consent grant
    with Session(engine) as db:
        p = Patient(
            mrn="CARRY-01",
            full_name="Carry Over Patient",
            gender=GenderEnum.FEMALE,
            date_of_birth=now - timedelta(days=10000),
            is_active=True,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        patient_id = p.id

    # Create v1 policy
    v1_doc = json.loads(
        (FIXTURES / "privacy_policy_strict.json").read_text(encoding="utf-8")
    )
    v1_doc["allow_care_flow_consent_gating"] = True
    c1 = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json={"policy_type": "CONSENT", "policy_document": v1_doc},
    )
    assert c1.status_code == 201
    rev1_id = c1.json()["id"]

    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        r1 = db.get(PrivacyPolicyRevision, rev1_id)
        r1.configured_by_user_id = doctor.id
        db.commit()

    a1 = client.post(
        f"/v1/api/privacy/policies/{rev1_id}/approve",
        headers=superuser_headers,
        json={"approval_reference": "V1-APP"},
    )
    assert a1.status_code == 200

    act1 = client.post(
        f"/v1/api/privacy/policies/{rev1_id}/activate-audit-only",
        headers=superuser_headers,
        json={"approval_reference": "V1-ACT"},
    )
    assert act1.status_code == 200

    # Record consent under v1
    c_res = client.post(
        f"/v1/api/privacy/patients/{patient_id}/consents",
        headers=superuser_headers,
        json={
            "consent_type": "DATA_SHARING",
            "purpose": "EXTERNAL_SHARING",
            "decision": "GRANTED",
            "source": "WRITTEN",
            "evidence_reference": "PAPER-CONSENT-FORM-01",
            "scope_type": "recipient",
            "scope_key": "partner-lab-01",
            "consenter_type": "patient",
        },
    )
    assert c_res.status_code == 201

    # Create v2 policy
    v2_doc = json.loads(
        (FIXTURES / "privacy_policy_strict.json").read_text(encoding="utf-8")
    )
    v2_doc["policy_code"] = "strict-v2"
    v2_doc["allow_care_flow_consent_gating"] = True
    c2 = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json={"policy_type": "CONSENT", "policy_document": v2_doc},
    )
    assert c2.status_code == 201
    rev2_id = c2.json()["id"]

    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        r2 = db.get(PrivacyPolicyRevision, rev2_id)
        r2.configured_by_user_id = doctor.id
        db.commit()

    a2 = client.post(
        f"/v1/api/privacy/policies/{rev2_id}/approve",
        headers=superuser_headers,
        json={"approval_reference": "V2-APP"},
    )
    assert a2.status_code == 200

    # Activate v2 WITH carry-over from v1
    act2 = client.post(
        f"/v1/api/privacy/policies/{rev2_id}/activate-audit-only",
        headers=superuser_headers,
        json={
            "approval_reference": "V2-ACT-CARRYOVER",
            "carry_over_from_revision_id": rev1_id,
        },
    )
    assert act2.status_code == 200

    # Verify that patient now has active consent under rev2!
    with Session(engine) as db:
        carried = (
            db.query(PatientConsentRecord)
            .filter_by(
                patient_id=patient_id,
                policy_revision_id=rev2_id,
                decision="GRANTED",
            )
            .first()
        )
        assert carried is not None
        assert carried.purpose == "EXTERNAL_SHARING"
        assert "CARRYOVER" in carried.evidence_reference



def test_legal_hold_alias_matching_stops_retention_under_any_alias(
    client, multi_tenant_env, superuser_headers, monkeypatch
):
    # N-02: A hold on 'patients' must protect when running retention as 'inactive-patients'
    monkeypatch.setattr(settings, "PRIVACY_ALLOW_RETENTION_EXECUTE", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    now = datetime.now(timezone.utc)
    old_date = now - timedelta(days=400)

    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        p = Patient(
            mrn="ALIAS-HELD-01",
            full_name="Alias Protected Patient",
            gender=GenderEnum.FEMALE,
            phone_number="+919876543111",
            date_of_birth=old_date,
            is_active=False,
            updated_at=old_date,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        p_id = p.id

        # Place hold using canonical 'patients' category
        db.add(
            PrivacyLegalHold(
                patient_id=p_id,
                data_category="patients",
                reason="Litigation hold on patients",
                hold_reference="HOLD-ALIAS-001",
                created_by_user_id=doctor.id,
            )
        )
        db.commit()

    retention_doc = {
        "schema_version": 1,
        "policy_code": "retention-alias-test",
        "categories": [
            {
                "code": "inactive-patients",
                "system_of_record": "Tenant Database",
                "locations": ["patients"],
                "retention_days": 365,
                "clock_start": "last_activity",
                "action": "anonymise",
                "legal_hold_triggers": ["court_order"],
                "backup_treatment": "Retain backup snapshots.",
                "export_treatment": "Exclude from exports.",
            }
        ],
    }
    c_res = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json={"policy_type": "RETENTION", "policy_document": retention_doc},
    )
    rev_id = c_res.json()["id"]

    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        r = db.get(PrivacyPolicyRevision, rev_id)
        r.configured_by_user_id = doctor.id
        db.commit()

    client.post(
        f"/v1/api/privacy/policies/{rev_id}/approve",
        headers=superuser_headers,
        json={"approval_reference": "ALIAS-APP"},
    )

    # Execute retention as 'inactive-patients'
    exec_res = client.post(
        "/v1/api/privacy/retention/execute",
        headers=superuser_headers,
        json={
            "policy_revision_id": rev_id,
            "categories": ["inactive-patients"],
            "approval_reference": "EXEC-ALIAS-001",
            "dry_run": False,
            "confirm_destructive_execution": True,
        },
    )
    assert exec_res.status_code == 200, exec_res.text
    res_data = exec_res.json()
    assert res_data["total_held"] >= 1

    # Verify patient was NOT anonymized because the hold on 'patients' protected them
    with Session(engine) as db:
        pat_after = db.get(Patient, p_id)
        assert pat_after.full_name == "Alias Protected Patient"


def test_retention_hard_delete_refuses_patients_with_clinical_encounters(
    client, multi_tenant_env, superuser_headers, monkeypatch
):
    # N-03: Hard delete must refuse to delete patients who have clinical encounter dependencies
    from backend.domains.encounters.models import (
        PatientEncounter,
        EncounterTypeEnum,
        EncounterStatusEnum,
    )
    from backend.domains.identity.models import Department

    monkeypatch.setattr(settings, "PRIVACY_ALLOW_RETENTION_EXECUTE", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    now = datetime.now(timezone.utc)
    old_date = now - timedelta(days=500)

    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        dept = db.query(Department).first()

        p_with_enc = Patient(
            mrn="HARD-DEL-ENC",
            full_name="Patient With Clinical Encounter",
            gender=GenderEnum.MALE,
            date_of_birth=old_date,
            is_active=False,
            updated_at=old_date,
        )
        p_empty = Patient(
            mrn="HARD-DEL-EMPTY",
            full_name="Empty Inactive Patient",
            gender=GenderEnum.FEMALE,
            date_of_birth=old_date,
            is_active=False,
            updated_at=old_date,
        )
        db.add_all([p_with_enc, p_empty])
        db.commit()
        db.refresh(p_with_enc)
        db.refresh(p_empty)

        enc = PatientEncounter(
            patient_id=p_with_enc.id,
            encounter_type=EncounterTypeEnum.OPD,
            status=EncounterStatusEnum.DISCHARGED,
            start_time=old_date,
            end_time=old_date,
            primary_doctor_id=doctor.id,
            department_id=dept.id,
        )
        db.add(enc)
        db.commit()
        p_enc_id = p_with_enc.id
        p_empty_id = p_empty.id

    retention_doc = {
        "schema_version": 1,
        "policy_code": "retention-delete-safety",
        "categories": [
            {
                "code": "patients",
                "system_of_record": "Tenant Database",
                "locations": ["patients"],
                "retention_days": 365,
                "clock_start": "last_activity",
                "action": "delete",
                "legal_hold_triggers": [],
                "backup_treatment": "Exclude.",
                "export_treatment": "Exclude.",
            }
        ],
    }
    c_res = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json={"policy_type": "RETENTION", "policy_document": retention_doc},
    )
    rev_id = c_res.json()["id"]

    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        r = db.get(PrivacyPolicyRevision, rev_id)
        r.configured_by_user_id = doctor.id
        db.commit()

    client.post(
        f"/v1/api/privacy/policies/{rev_id}/approve",
        headers=superuser_headers,
        json={"approval_reference": "DEL-SAFE-APP"},
    )

    exec_res = client.post(
        "/v1/api/privacy/retention/execute",
        headers=superuser_headers,
        json={
            "policy_revision_id": rev_id,
            "categories": ["patients"],
            "approval_reference": "DEL-SAFE-EXEC",
            "dry_run": False,
            "confirm_destructive_execution": True,
        },
    )
    assert exec_res.status_code == 200

    # Verify: Patient with encounter is NOT deleted; Patient without encounter IS deleted!
    with Session(engine) as db:
        assert db.get(Patient, p_enc_id) is not None
        assert db.get(Patient, p_empty_id) is None


def test_consent_carry_over_does_not_resurrect_withdrawn_consent(
    client, multi_tenant_env, superuser_headers, monkeypatch
):
    # N-04: Withdrawn consent under v1 must NOT be carried over to v2 as granted
    monkeypatch.setattr(settings, "PRIVACY_ALLOW_AUDIT_ONLY", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    now = datetime.now(timezone.utc)

    with Session(engine) as db:
        p_withdrawn = Patient(
            mrn="WITHDRAWN-01",
            full_name="Withdrawn Patient",
            gender=GenderEnum.FEMALE,
            date_of_birth=now - timedelta(days=10000),
            is_active=True,
        )
        p_granted = Patient(
            mrn="GRANTED-01",
            full_name="Granted Patient",
            gender=GenderEnum.MALE,
            date_of_birth=now - timedelta(days=10000),
            is_active=True,
        )
        db.add_all([p_withdrawn, p_granted])
        db.commit()
        db.refresh(p_withdrawn)
        db.refresh(p_granted)
        p_w_id = p_withdrawn.id
        p_g_id = p_granted.id

    v1_doc = json.loads((FIXTURES / "privacy_policy_strict.json").read_text(encoding="utf-8"))
    v1_doc["allow_care_flow_consent_gating"] = True
    c1 = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json={"policy_type": "CONSENT", "policy_document": v1_doc},
    )
    rev1_id = c1.json()["id"]

    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        r1 = db.get(PrivacyPolicyRevision, rev1_id)
        r1.configured_by_user_id = doctor.id
        db.commit()

    client.post(f"/v1/api/privacy/policies/{rev1_id}/approve", headers=superuser_headers, json={"approval_reference": "V1-APP"})
    client.post(f"/v1/api/privacy/policies/{rev1_id}/activate-audit-only", headers=superuser_headers, json={"approval_reference": "V1-ACT"})

    # 1. p_granted grants consent
    client.post(
        f"/v1/api/privacy/patients/{p_g_id}/consents",
        headers=superuser_headers,
        json={
            "consent_type": "DATA_SHARING",
            "purpose": "EXTERNAL_SHARING",
            "decision": "GRANTED",
            "source": "WRITTEN",
            "evidence_reference": "GRANT-DOC-01",
            "scope_type": "recipient",
            "scope_key": "lab-01",
            "consenter_type": "patient",
        },
    )

    # 2. p_withdrawn grants then withdraws consent
    client.post(
        f"/v1/api/privacy/patients/{p_w_id}/consents",
        headers=superuser_headers,
        json={
            "consent_type": "DATA_SHARING",
            "purpose": "EXTERNAL_SHARING",
            "decision": "GRANTED",
            "source": "WRITTEN",
            "evidence_reference": "GRANT-DOC-02",
            "scope_type": "recipient",
            "scope_key": "lab-01",
            "consenter_type": "patient",
        },
    )
    client.post(
        f"/v1/api/privacy/patients/{p_w_id}/consents",
        headers=superuser_headers,
        json={
            "consent_type": "DATA_SHARING",
            "purpose": "EXTERNAL_SHARING",
            "decision": "WITHDRAWN",
            "source": "WRITTEN",
            "evidence_reference": "WITHDRAW-DOC-01",
            "scope_type": "recipient",
            "scope_key": "lab-01",
            "consenter_type": "patient",
        },
    )

    # Create & approve v2
    v2_doc = json.loads((FIXTURES / "privacy_policy_strict.json").read_text(encoding="utf-8"))
    v2_doc["policy_code"] = "strict-v2"
    v2_doc["allow_care_flow_consent_gating"] = True
    c2 = client.post(
        "/v1/api/privacy/policies",
        headers=superuser_headers,
        json={"policy_type": "CONSENT", "policy_document": v2_doc},
    )
    rev2_id = c2.json()["id"]

    with Session(engine) as db:
        doctor = db.query(User).filter_by(email="doctor@default.com").one()
        r2 = db.get(PrivacyPolicyRevision, rev2_id)
        r2.configured_by_user_id = doctor.id
        db.commit()

    client.post(f"/v1/api/privacy/policies/{rev2_id}/approve", headers=superuser_headers, json={"approval_reference": "V2-APP"})

    # Activate v2 with carry-over
    act2 = client.post(
        f"/v1/api/privacy/policies/{rev2_id}/activate-audit-only",
        headers=superuser_headers,
        json={
            "approval_reference": "V2-ACT-CARRYOVER",
            "carry_over_from_revision_id": rev1_id,
        },
    )
    assert act2.status_code == 200

    # Verify: p_granted HAS carried-over consent, p_withdrawn DOES NOT have consent!
    with Session(engine) as db:
        g_c = db.query(PatientConsentRecord).filter_by(patient_id=p_g_id, policy_revision_id=rev2_id).first()
        assert g_c is not None
        assert g_c.decision == "GRANTED"

        w_c = db.query(PatientConsentRecord).filter_by(patient_id=p_w_id, policy_revision_id=rev2_id).first()
        assert w_c is None
