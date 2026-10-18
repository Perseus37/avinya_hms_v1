import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from backend.control_plane.models import ControlBase
from backend.core.database import Base
from backend.scripts.migrate import (
    ensure_metadata_schema,
    get_current_db_revision,
    upgrade_control_plane_database_url,
    upgrade_tenant_database_url,
    validate_tenant_database_url,
)

CONTROL_PLANE_HEAD = "1c8b4a72e903"


def test_control_plane_initial_migration_matches_metadata(tmp_path):
    database_path = tmp_path / "control_plane.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("backend/control_plane/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
            diffs = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}),
                ControlBase.metadata,
            )

        assert tables == set(ControlBase.metadata.tables)
        assert diffs == []
    finally:
        engine.dispose()


def test_tenant_initial_migration_matches_metadata(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'tenant.sqlite').as_posix()}"

    upgrade_tenant_database_url(database_url)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
            diffs = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}),
                Base.metadata,
            )
        assert tables == set(Base.metadata.tables)
        assert diffs == []
    finally:
        engine.dispose()


def test_production_tenant_validation_requires_head_and_schema_parity(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'production_tenant.sqlite').as_posix()}"
    upgrade_tenant_database_url(database_url)
    current_revision = get_current_db_revision(database_url)

    validate_tenant_database_url(database_url)

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE alembic_version SET version_num = 'not-current'"
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="does not match checked-in head"):
        validate_tenant_database_url(database_url)

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE alembic_version SET version_num = ?",
                (current_revision,),
            )
            connection.exec_driver_sql("DROP TABLE procurement_vendors")
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="differs from the current ORM metadata"):
        validate_tenant_database_url(database_url)


def test_control_plane_upgrade_adopts_unversioned_schema_with_existing_blueprint_table(
    tmp_path,
):
    database_path = tmp_path / "legacy_control_plane.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    try:
        ControlBase.metadata.tables["blueprint_catalog"].create(engine)
    finally:
        engine.dispose()

    upgrade_control_plane_database_url(database_url)

    assert get_current_db_revision(database_url) == CONTROL_PLANE_HEAD
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            diffs = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}),
                ControlBase.metadata,
            )
        assert diffs == []
    finally:
        engine.dispose()

    upgrade_control_plane_database_url(database_url)


def test_control_plane_upgrade_uses_configured_url_over_environment(
    tmp_path, monkeypatch
):
    configured_path = tmp_path / "configured_control_plane.sqlite"
    environment_path = tmp_path / "environment_control_plane.sqlite"
    configured_url = f"sqlite:///{configured_path.as_posix()}"
    environment_url = f"sqlite:///{environment_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", environment_url)

    upgrade_control_plane_database_url(configured_url)

    configured_engine = create_engine(configured_url)
    environment_engine = create_engine(environment_url)
    try:
        assert set(inspect(configured_engine).get_table_names()) - {"alembic_version"}
        assert inspect(environment_engine).get_table_names() == []
    finally:
        configured_engine.dispose()
        environment_engine.dispose()


def test_control_plane_upgrade_rejects_drift_in_versioned_schema(tmp_path):
    database_path = tmp_path / "drifted_control_plane.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("backend/control_plane/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE blueprint_catalog")
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="still differs from the current ORM schema"):
        upgrade_control_plane_database_url(database_url)


def test_dev_bootstrap_recreates_empty_databases_without_revision_files(
    tmp_path, monkeypatch
):
    from backend.scripts import migrate

    monkeypatch.setattr(migrate, "_migration_revisions_available", lambda config: False)
    control_url = f"sqlite:///{(tmp_path / 'new_control.sqlite').as_posix()}"
    tenant_url = f"sqlite:///{(tmp_path / 'new_tenant.sqlite').as_posix()}"

    upgrade_control_plane_database_url(control_url, allow_metadata_create=True)
    upgrade_tenant_database_url(tenant_url, allow_metadata_create=True)

    for database_url, metadata in (
        (control_url, ControlBase.metadata),
        (tenant_url, Base.metadata),
    ):
        engine = create_engine(database_url)
        try:
            assert set(inspect(engine).get_table_names()) >= set(metadata.tables)
        finally:
            engine.dispose()


def test_dev_bootstrap_reuses_matching_schema_without_revision_files(
    tmp_path, monkeypatch
):
    from backend.scripts import migrate

    monkeypatch.setattr(migrate, "_migration_revisions_available", lambda config: False)
    database_url = f"sqlite:///{(tmp_path / 'existing_schema.sqlite').as_posix()}"

    upgrade_control_plane_database_url(database_url, allow_metadata_create=True)
    upgrade_control_plane_database_url(database_url, allow_metadata_create=True)

    engine = create_engine(database_url)
    try:
        assert set(inspect(engine).get_table_names()) >= set(ControlBase.metadata.tables)
    finally:
        engine.dispose()


def test_schema_autocreate_refuses_existing_drift_without_dropping_data(tmp_path):
    from sqlalchemy import Column, Integer, MetaData, String, Table, text

    database_url = f"sqlite:///{(tmp_path / 'drift.sqlite').as_posix()}"
    metadata = MetaData()
    Table(
        "preserved_records",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("value", String, nullable=False),
    )
    engine = create_engine(database_url)
    try:
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO preserved_records (id, value) VALUES (1, 'keep')")
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="does not match the current ORM schema"):
        ensure_metadata_schema(database_url, ControlBase.metadata, allow_create=True)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT value FROM preserved_records WHERE id = 1")
            ).scalar_one() == "keep"
    finally:
        engine.dispose()


@pytest.mark.parametrize("default_exists", [True, False])
def test_production_bootstrap_migrates_only_a_new_default_tenant(
    monkeypatch, default_exists
):
    from backend.scripts import bootstrap

    existing_tenant = type(
        "Tenant",
        (),
        {
            "subdomain_slug": bootstrap.settings.DEFAULT_TENANT_SLUG,
            "db_name": (
                f"{bootstrap.settings.TENANT_DATABASE_PREFIX}"
                f"{bootstrap.settings.DEFAULT_TENANT_SLUG}"
            ),
            "is_active": True,
            "deployment_mode": bootstrap.DeploymentModeEnum.CLOUD,
            "hospital_name": "Existing Hospital",
            "branch_code": "EXISTING-01",
            "admin_email": "admin@example.invalid",
        },
    )()
    created_tenants = []
    query_count = 0

    class Result:
        def __init__(self, single=None, items=None):
            self.single = single
            self.items = items or []

        def scalar_one_or_none(self):
            return self.single

        def scalar_one(self):
            return self.single

        def scalars(self):
            return self

        def all(self):
            return self.items

    class ControlSession:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, _query):
            nonlocal query_count
            query_count += 1
            if default_exists:
                if query_count == 1:
                    return Result(single=existing_tenant)
                return Result(items=[existing_tenant])
            if query_count == 1:
                return Result()
            return Result(items=created_tenants)

        def add(self, tenant):
            created_tenants.append(tenant)

        def commit(self):
            pass

    class TenantSession:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def commit(self):
            pass

    class Engine:
        def dispose(self):
            pass

    tenant_upgrades = []
    tenant_validations = []
    monkeypatch.setattr(bootstrap, "provision_physical_databases", lambda: None)
    monkeypatch.setattr(bootstrap, "ControlSessionLocal", ControlSession)
    monkeypatch.setattr(bootstrap, "upgrade_control_plane_database_url", lambda _: None)
    monkeypatch.setattr(bootstrap, "build_db_url", lambda name: f"postgresql:///{name}")
    monkeypatch.setattr(bootstrap, "seed_control_plane", lambda _: None)
    monkeypatch.setattr(
        bootstrap,
        "encrypt_tenant_secret",
        lambda secret: f"encrypted:{secret}",
    )
    monkeypatch.setattr(
        bootstrap,
        "upgrade_tenant_database_url",
        lambda url: tenant_upgrades.append(url),
    )
    monkeypatch.setattr(
        bootstrap,
        "validate_tenant_database_url",
        lambda url: tenant_validations.append(url),
    )
    monkeypatch.setattr(bootstrap, "create_engine", lambda _: Engine())
    monkeypatch.setattr(
        bootstrap,
        "sessionmaker",
        lambda **_: lambda: TenantSession(),
    )
    monkeypatch.setattr(bootstrap, "ensure_permissions_exist", lambda _: None)
    monkeypatch.setattr(bootstrap, "seed_universal_hospital_data", lambda **_: None)
    monkeypatch.setattr(bootstrap.settings, "ENVIRONMENT", "production")

    bootstrap.bootstrap_system()

    if default_exists:
        assert tenant_upgrades == []
    else:
        assert len(tenant_upgrades) == 1
    expected_url = (
        f"postgresql:///{bootstrap.settings.TENANT_DATABASE_PREFIX}"
        f"{bootstrap.settings.DEFAULT_TENANT_SLUG}"
    )
    assert tenant_validations == [expected_url]


def test_fleet_migration_requires_a_verified_canary_before_upgrades(monkeypatch):
    from backend.scripts import migrate

    upgrade_attempts = []
    monkeypatch.setattr(
        migrate,
        "ensure_postgres_db_exists",
        lambda db_name: f"postgresql:///{db_name}",
    )

    def reject_canary(_database_url):
        raise RuntimeError("canary is not at head")

    monkeypatch.setattr(migrate, "validate_tenant_database_url", reject_canary)
    monkeypatch.setattr(
        migrate,
        "upgrade_single_database",
        lambda db_name: upgrade_attempts.append(db_name),
    )

    with pytest.raises(RuntimeError, match="canary is not at head"):
        migrate.migrate_remaining_fleet()

    assert upgrade_attempts == []


def test_migration_cli_returns_failure_when_a_tenant_upgrade_fails(monkeypatch):
    from backend.scripts import migrate

    monkeypatch.setattr(
        migrate,
        "migrate_remaining_fleet",
        lambda target_slug=None: {
            "upgraded": [],
            "failed": [{"subdomain_slug": "tenant", "error": "migration failed"}],
        },
    )

    assert migrate.main(["--fleet"]) == 1


def test_schema_autocreate_requires_explicit_opt_in(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'no_opt_in.sqlite').as_posix()}"

    with pytest.raises(RuntimeError, match="Schema auto-creation is disabled"):
        ensure_metadata_schema(
            database_url, ControlBase.metadata, allow_create=False
        )
