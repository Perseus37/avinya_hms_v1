import json
from decimal import Decimal
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker

from backend.control_plane.app import app as control_plane_app
from backend.control_plane.api.routers import fleet_ops, provisioning
from backend.control_plane.api.routers import migration as migration_router
from backend.control_plane.models import TenantRegistry
from backend.control_plane.services.auth import verify_control_plane_secret
from backend.control_plane.services.pricing import (
    get_blueprint_catalog,
    get_bundle_catalog,
    get_module_catalog,
)
from backend.control_plane.services.catalogs import seed_control_plane
from backend.control_plane.services.migration_catalog import get_migration_models
from backend.main import app as tenant_api_app
from backend.core.config import settings
from backend.core.database import get_control_db
from backend.core.database import Base, tenant_engine_pool
from backend.control_plane.api.routers.migration import (
    _apply_dynamic_mapping,
    _evaluate_expression,
    _single_column_unique_fields,
    _validate_duplicate_settings,
    _validate_mapping,
)
from backend.domains.billing.models import Invoice
from backend.domains.patients.models import Patient
from backend.domains.identity.models import GenderEnum


def test_control_plane_catalog_seed_is_idempotent_and_preserves_edits(multi_tenant_env):
    control_session_factory = multi_tenant_env["control_session_factory"]

    with control_session_factory() as db:
        db.execute(
            text("UPDATE module_catalog SET base_annual_price_paise = 123 WHERE code = 'opd'")
        )
        db.commit()

        seed_control_plane(db)

        assert len(get_module_catalog(db)) == 12
        assert get_module_catalog(db)["opd"]["base_annual_price_paise"] == 123
        assert len(get_bundle_catalog(db)) == 6
        assert len(get_blueprint_catalog(db)) == 5
        assert db.scalar(text("SELECT COUNT(*) FROM migration_schemas")) == 3
        patient_schema = db.scalar(
            text("SELECT schema_json FROM migration_schemas WHERE entity_type = 'PATIENTS'")
        )
        assert set(json.loads(patient_schema)) == {
            "mrn",
            "full_name",
            "date_of_birth",
            "gender",
            "phone_number",
        }


def test_migration_mapping_only_adds_fields_supported_by_destination_model():
    patient = _apply_dynamic_mapping(
        {"full_name": "A Patient", "gender": "F", "dob": "1980-01-02"},
        {
            "full_name": {"source_column": "full_name"},
            "gender": {"source_column": "gender"},
            "date_of_birth": {"source_column": "dob"},
        },
        Patient,
    )
    invoice = _apply_dynamic_mapping(
        {"number": "INV-1", "mrn": "MRN-1", "total": "100"},
        {
            "invoice_number": {"source_column": "number"},
            "patient_mrn": {"source_column": "mrn"},
            "total_amount_paise": {"source_column": "total"},
        },
        Invoice,
    )

    assert patient["is_active"] is True
    assert patient["allow_network_sharing"] is True
    assert patient["gender"].value == "FEMALE"
    assert patient["date_of_birth"].isoformat().startswith("1980-01-02")
    assert "allow_network_sharing" not in invoice
    assert "is_active" not in invoice
    assert invoice["patient_mrn"] == "MRN-1"
    _validate_mapping(
        {
            "full_name": {"source_column": "full_name"},
            "gender": {"source_column": "gender"},
            "date_of_birth": {"source_column": "dob"},
        },
        Patient,
    )


def test_migration_etl_expressions_support_combine_transform_and_condition():
    expression = {
        "type": "transform",
        "operation": "conditional",
        "operator": "equals",
        "compareValue": "ada lovelace",
        "whenTrue": "PATIENT",
        "whenFalse": "OTHER",
        "input": {
            "type": "transform",
            "operation": "uppercase",
            "input": {
                "type": "combine",
                "separator": " ",
                "inputs": [
                    {"type": "source", "column": "first"},
                    {
                        "type": "transform",
                        "operation": "trim",
                        "input": {"type": "source", "column": "last"},
                    },
                ],
            },
        },
    }
    assert _evaluate_expression(expression, {"first": "Ada", "last": " Lovelace "}) == "PATIENT"

    mapped_value = {
        "type": "transform",
        "operation": "map",
        "map": {"F": "FEMALE", "M": "MALE"},
        "input": {"type": "source", "column": "legacy_gender"},
    }
    assert _evaluate_expression(mapped_value, {"legacy_gender": "m"}) == "MALE"

    patient = _apply_dynamic_mapping(
        {"first": "Ada", "last": "Lovelace"},
        {
            "full_name": {
                "expression": {
                    "type": "combine",
                    "separator": " ",
                    "inputs": [
                        {"type": "source", "column": "first"},
                        {"type": "source", "column": "last"},
                    ],
                }
            },
            "gender": {
                "expression": {
                    "type": "transform",
                    "operation": "map",
                    "map": {"F": "FEMALE"},
                    "input": {"type": "source", "column": "legacy_gender"},
                },
                "fallback": "F",
            },
            "date_of_birth": {
                "expression": {"type": "constant", "value": "1988-12-10"}
            },
        },
        Patient,
    )
    assert patient["full_name"] == "Ada Lovelace"
    assert patient["gender"].value == "FEMALE"
    assert patient["date_of_birth"].isoformat().startswith("1988-12-10")


def test_migration_etl_supports_coalesce_numeric_text_and_date_operations():
    row = {"empty": "", "amount": "12.5", "code": "AB-123", "legacy_date": "31/12/2024"}
    coalesced = {
        "type": "coalesce",
        "inputs": [
            {"type": "source", "column": "empty"},
            {"type": "source", "column": "amount"},
        ],
    }
    assert _evaluate_expression(coalesced, row) == "12.5"
    assert _evaluate_expression(
        {
            "type": "transform",
            "operation": "add",
            "operand": "2.5",
            "input": {"type": "source", "column": "amount"},
        },
        row,
    ) == Decimal("15.0")
    assert _evaluate_expression(
        {
            "type": "transform",
            "operation": "split_part",
            "delimiter": "-",
            "part_index": 1,
            "input": {"type": "source", "column": "code"},
        },
        row,
    ) == "123"
    assert _evaluate_expression(
        {
            "type": "transform",
            "operation": "parse_date",
            "input_format": "%d/%m/%Y",
            "input": {"type": "source", "column": "legacy_date"},
        },
        row,
    ) == "2024-12-31"


def test_migration_duplicate_policy_requires_mapped_unique_key():
    unique_fields = _single_column_unique_fields(Patient)
    assert "mrn" in unique_fields
    _validate_duplicate_settings(
        Patient, "skip", "mrn", {"mrn": {"source_column": "legacy_mrn"}}
    )
    try:
        _validate_duplicate_settings(
            Patient, "skip", "mrn", {"full_name": {"source_column": "name"}}
        )
    except ValueError as error:
        assert "must be mapped" in str(error)
    else:
        raise AssertionError("Duplicate matching must use a mapped unique field.")
    _validate_duplicate_settings(Patient, "allow", None, {})


def test_migration_worker_updates_existing_unique_match_and_audits_actor(
    multi_tenant_env, monkeypatch, tmp_path
):
    engine = multi_tenant_env["tenant_engines"]["hms_apollo"]
    control_session_factory = multi_tenant_env["control_session_factory"]
    monkeypatch.setattr(
        migration_router.tenant_engine_pool, "get_engine", lambda _db_name: engine
    )
    monkeypatch.setattr(
        migration_router, "ControlSessionLocal", control_session_factory
    )
    source_mrn = "MIGRATION-UPDATE-TEST"
    with sessionmaker(bind=engine)() as db:
        db.add(
            Patient(
                mrn=source_mrn,
                full_name="Old Name",
                date_of_birth=datetime(1990, 1, 1),
                gender=GenderEnum.FEMALE,
            )
        )
        db.commit()

    csv_path = tmp_path / "update.csv"
    csv_path.write_text(
        "mrn,name,dob,gender\n"
        f"{source_mrn},Updated Name,1990-01-01,FEMALE\n",
        encoding="utf-8",
    )
    mapping = {
        "mrn": {"source_column": "mrn"},
        "full_name": {"source_column": "name"},
        "date_of_birth": {"source_column": "dob"},
        "gender": {"source_column": "gender"},
    }
    migration_router._execute_bulk_migration_background(
        "hms_apollo",
        "apollo",
        Patient,
        mapping,
        str(csv_path),
        "update",
        "mrn",
    )

    with sessionmaker(bind=engine)() as db:
        patient = db.query(Patient).filter_by(mrn=source_mrn).one()
        assert patient.full_name == "Updated Name"
        assert patient.created_by_id is None
        assert patient.updated_by_id is None
        assert patient.created_at is not None and patient.updated_at is not None

    with control_session_factory() as db:
        audit = (
            db.query(migration_router.ControlPlaneAuditLog)
            .filter_by(action="ETL_VISUAL_MIGRATION_COMPLETED", target_slug="apollo")
            .one()
        )
        assert audit.actor == "VENDOR_CONSOLE"
        assert '"updated": 1' in audit.details


def test_migration_worker_skips_or_rejects_duplicate_rows_without_overwriting(
    multi_tenant_env, monkeypatch, tmp_path
):
    engine = multi_tenant_env["tenant_engines"]["hms_apollo"]
    control_session_factory = multi_tenant_env["control_session_factory"]
    monkeypatch.setattr(
        migration_router.tenant_engine_pool, "get_engine", lambda _db_name: engine
    )
    monkeypatch.setattr(
        migration_router, "ControlSessionLocal", control_session_factory
    )
    source_mrn = "MIGRATION-DUPLICATE-POLICY-TEST"
    with sessionmaker(bind=engine)() as db:
        db.add(
            Patient(
                mrn=source_mrn,
                full_name="Original Name",
                date_of_birth=datetime(1990, 1, 1),
                gender=GenderEnum.FEMALE,
            )
        )
        db.commit()

    csv_path = tmp_path / "duplicates.csv"
    csv_path.write_text(
        "mrn,name,dob,gender\n"
        f"{source_mrn},Imported One,1990-01-01,FEMALE\n"
        f"{source_mrn},Imported Two,1990-01-01,FEMALE\n",
        encoding="utf-8",
    )
    mapping = {
        "mrn": {"source_column": "mrn"},
        "full_name": {"source_column": "name"},
        "date_of_birth": {"source_column": "dob"},
        "gender": {"source_column": "gender"},
    }
    for policy, count_name in (("skip", "skipped"), ("reject", "rejected")):
        csv_path.write_text(
            "mrn,name,dob,gender\n"
            f"{source_mrn},Imported One,1990-01-01,FEMALE\n"
            f"{source_mrn},Imported Two,1990-01-01,FEMALE\n",
            encoding="utf-8",
        )
        migration_router._execute_bulk_migration_background(
            "hms_apollo",
            "apollo",
            Patient,
            mapping,
            str(csv_path),
            policy,
            "mrn",
        )
        with control_session_factory() as db:
            audit = (
                db.query(migration_router.ControlPlaneAuditLog)
                .filter_by(
                    action="ETL_VISUAL_MIGRATION_COMPLETED",
                    target_slug="apollo",
                )
                .order_by(migration_router.ControlPlaneAuditLog.id.desc())
                .first()
            )
            assert f'"{count_name}": 2' in audit.details

    with sessionmaker(bind=engine)() as db:
        patient = db.query(Patient).filter_by(mrn=source_mrn).one()
        assert patient.full_name == "Original Name"


def test_migration_rejects_unsupported_etl_operations():
    invalid = {
        "type": "transform",
        "operation": "python",
        "input": {"type": "source", "column": "full_name"},
    }
    try:
        _evaluate_expression(invalid, {"full_name": "A Patient"})
    except ValueError as error:
        assert "Unsupported transformation" in str(error)
    else:
        raise AssertionError("Arbitrary ETL operations must not be evaluated.")


def test_onboarding_catalog_api_returns_seeded_catalogs_and_migration_schemas(
    multi_tenant_env, monkeypatch
):
    control_session_factory = multi_tenant_env["control_session_factory"]

    def override_control_db():
        with control_session_factory() as db:
            yield db

    monkeypatch.setitem(
        control_plane_app.dependency_overrides, get_control_db, override_control_db
    )
    monkeypatch.setattr(settings, "CONTROL_PLANE_MASTER_KEY", "test-control-plane-key")

    with TestClient(control_plane_app) as client:
        headers = {"X-Control-Plane-Secret": "test-control-plane-key"}
        pricing = client.get("/api/pricing/catalog", headers=headers)
        blueprints = client.get("/api/blueprints", headers=headers)

    assert pricing.status_code == 200
    assert len(pricing.json()["modules"]) == 12
    assert len(pricing.json()["bundles"]) == 6
    migration_schemas = pricing.json()["migrationSchemas"]
    assert set(migration_schemas) == set(get_migration_models())
    assert len(migration_schemas) > 3
    assert all(isinstance(fields, list) for fields in migration_schemas.values())
    assert all(
        "id" in field and "type" in field
        for fields in migration_schemas.values()
        for field in fields
    )
    federation_fields = {
        field["id"] for field in migration_schemas["FEDERATED_CLIENTS"]
    }
    assert "api_key_hash" not in federation_fields
    assert "api_key_prefix" not in federation_fields
    assert blueprints.status_code == 200
    assert len(blueprints.json()["blueprints"]) == 5


def test_onboarding_catalog_api_rejects_invalid_control_plane_secret(monkeypatch):
    monkeypatch.setattr(settings, "CONTROL_PLANE_MASTER_KEY", "test-control-plane-key")

    with TestClient(control_plane_app) as client:
        response = client.get(
            "/api/pricing/catalog",
            headers={"X-Control-Plane-Secret": "invalid"},
        )

    assert response.status_code == 401


def test_tenant_url_uses_environment_template(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "BASE_DOMAIN", "localhost")
    monkeypatch.setattr(settings, "TENANT_URL_TEMPLATE", None)
    monkeypatch.setattr(settings, "API_PORT", 8000)
    assert settings.tenant_url("clinic-one") == "http://clinic-one.localhost:8000"

    monkeypatch.setattr(settings, "API_PORT", 9000)
    assert settings.tenant_url("clinic-one") == "http://clinic-one.localhost:9000"
    assert settings.API_INTERNAL_URL == "http://api:9000"

    monkeypatch.setattr(settings, "API_PORT", 8000)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert settings.tenant_url("clinic-one") == "https://clinic-one.localhost"

    monkeypatch.setattr(
        settings, "TENANT_URL_TEMPLATE", "https://{slug}.care.example.test"
    )
    assert settings.tenant_url("clinic-one") == "https://clinic-one.care.example.test"


def test_tenant_root_redirects_to_api_docs():
    with TestClient(tenant_api_app) as client:
        response = client.get(
            "/",
            headers={"Host": "clinic-one.localhost"},
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"] == "/docs"


def test_provisioning_persists_and_returns_selected_configuration(
    multi_tenant_env, monkeypatch, tmp_path
):
    control_session_factory = multi_tenant_env["control_session_factory"]
    tenant_db_path = tmp_path / "provisioned_tenant.sqlite"
    tenant_engine = create_engine(f"sqlite:///{tenant_db_path.as_posix()}")
    Base.metadata.create_all(tenant_engine)

    def override_control_db():
        with control_session_factory() as db:
            yield db

    monkeypatch.setitem(
        control_plane_app.dependency_overrides, get_control_db, override_control_db
    )
    monkeypatch.setattr(settings, "CONTROL_PLANE_MASTER_KEY", "test-control-plane-key")
    monkeypatch.setattr(provisioning, "ensure_postgres_db_exists", lambda _: "sqlite://")
    monkeypatch.setattr(
        provisioning, "upgrade_tenant_database_url", lambda *_args: None
    )
    monkeypatch.setattr(provisioning, "create_engine", lambda *_args, **_kwargs: tenant_engine)
    monkeypatch.setattr(provisioning, "_dispatch_welcome_email", lambda *_args: False)
    monkeypatch.setattr(tenant_engine_pool, "get_engine", lambda _db_name: tenant_engine)
    monkeypatch.setattr(provisioning, "_dispatch_welcome_email", lambda *_args: (False, None))

    payload = {
        "master_setup_key": settings.MASTER_ONBOARDING_KEY,
        "hospital_name": "Readback Hospital",
        "subdomain_slug": "readback-hospital",
        "branch_code": "READBACK-01",
        "admin_email": "admin@readback.example",
        "pricing_model": "BUNDLE_OPD_DAYCARE",
        "billing_cycle": "MONTHLY_SUBSCRIPTION",
        "selected_modules": ["opd", "pharmacy", "billing"],
        "quota_max_beds": 24,
        "quota_max_users": 45,
        "blueprint_archetype": "POLYCLINIC_DAYCARE_OPD",
        "federation_sharing_mode": "ISOLATED_SILO",
        "seed_icd10": False,
        "seed_allergies": True,
        "seed_complaints": False,
    }

    try:
        with TestClient(control_plane_app) as client:
            headers = {"X-Control-Plane-Secret": "test-control-plane-key"}
            created = client.post("/api/provision", json=payload, headers=headers)
            assert created.status_code == 200, created.text

            tenants = client.get(
                "/api/tenants",
                headers={
                    **headers,
                    "X-Master-Setup-Key": settings.MASTER_ONBOARDING_KEY,
                },
            )
            details = client.get(
                "/api/tenants/readback-hospital/details",
                headers={
                    **headers,
                    "X-Master-Setup-Key": settings.MASTER_ONBOARDING_KEY,
                },
            )
    finally:
        tenant_engine.dispose()

    assert tenants.status_code == 200, tenants.text
    stored_tenant = next(
        tenant
        for tenant in tenants.json()["items"]
        if tenant["subdomain_slug"] == "readback-hospital"
    )
    assert stored_tenant["pricing_model"] == "BUNDLE_OPD_DAYCARE"
    assert stored_tenant["tenant_url"] == "http://readback-hospital.localhost:8000"
    assert stored_tenant["billing_cycle"] == "MONTHLY_SUBSCRIPTION"
    assert stored_tenant["quota_limits"] == {"max_beds": 0, "max_users": 45}
    assert stored_tenant["blueprint_archetype"] == "POLYCLINIC_DAYCARE_OPD"

    assert details.status_code == 200, details.text
    detail_data = details.json()
    assert detail_data["pricing_model"] == "BUNDLE_OPD_DAYCARE"
    assert detail_data["billing_cycle"] == "MONTHLY_SUBSCRIPTION"
    assert detail_data["quota_limits"] == {"max_beds": 0, "max_users": 45}
    assert detail_data["enabled_modules"]["opd"] is True
    assert detail_data["hospital_policy"]["federation_sharing_mode"] == "ISOLATED_SILO"
    assert detail_data["provisioning_config"] == {
        "blueprint_archetype": "POLYCLINIC_DAYCARE_OPD",
        "seed_icd10": False,
        "seed_allergies": True,
        "seed_complaints": False,
    }
    with control_session_factory() as db:
        row = db.query(TenantRegistry).filter_by(subdomain_slug="readback-hospital").one()
        assert row.pricing_model.value == "BUNDLE_OPD_DAYCARE"
        assert row.billing_cycle.value == "MONTHLY_SUBSCRIPTION"
