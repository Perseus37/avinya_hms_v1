import argparse
import os
from pathlib import Path
import sys

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BACKEND_DIR.parent
for p in (_REPO_DIR,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select

from backend.core.config import settings
from backend.control_plane.models import (
    ControlBase,
    ControlPlaneAuditLog,
    DeploymentModeEnum,
    TenantRegistry,
)
from backend.core.database import Base, ControlSessionLocal, ensure_postgres_db_exists
from backend.core.seed import ensure_permissions_exist
import backend.domains.diagnostics.models  # noqa: F401
import backend.domains.encounters.models  # noqa: F401
import backend.domains.facility.models  # noqa: F401
import backend.domains.identity.models  # noqa: F401
import backend.domains.patients.models  # noqa: F401
import backend.domains.billing.models  # noqa: F401
import backend.domains.pharmacy.models  # noqa: F401
import backend.domains.insurance.models  # noqa: F401
import backend.domains.clinical.models  # noqa: F401
import backend.domains.surgery.models  # noqa: F401
import backend.domains.bloodbank.models  # noqa: F401
import backend.domains.reports.models  # noqa: F401
import backend.domains.policies.models  # noqa: F401
import backend.domains.finance.models  # noqa: F401
import backend.domains.integrations.models  # noqa: F401
import backend.domains.federation.models  # noqa: F401
import backend.domains.procurement.models  # noqa: F401
import backend.domains.identity.models #noqa: F401


def _development_schema_autocreate_enabled() -> bool:
    enabled = settings.DEVELOPMENT_AUTO_CREATE_SCHEMA
    environment = settings.ENVIRONMENT.strip().lower()
    return enabled and environment in {"development", "dev", "testing"}


def _migration_revisions_available(config: Config) -> bool:
    script_location = Path(config.get_main_option("script_location"))
    if not script_location.is_absolute():
        config_path = Path(config.config_file_name or ".").resolve()
        script_location = config_path.parent / script_location
    versions_path = script_location / "versions"
    return versions_path.is_dir() and any(
        revision.is_file() and revision.suffix == ".py"
        for revision in versions_path.rglob("*.py")
    )


def ensure_metadata_schema(
    db_url: str, metadata, *, allow_create: bool = False
) -> None:
    """Create an empty development schema, or verify an existing schema unchanged."""
    if not allow_create:
        raise RuntimeError(
            "Alembic revisions are unavailable. Schema auto-creation is disabled; "
            "restore the migration files before starting this environment."
        )

    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
            existing_tables = tables - {"alembic_version"}
            if not existing_tables:
                metadata.create_all(bind=connection)

            diffs = compare_metadata(
                MigrationContext.configure(
                    connection, opts={"compare_type": True}
                ),
                metadata,
            )
            if diffs:
                raise RuntimeError(
                    "Development database does not match the current ORM schema. "
                    "No existing tables were modified; restore the matching "
                    f"migrations or reset this disposable database. Differences: {diffs!r}"
                )
    finally:
        engine.dispose()


def _get_alembic_cfg(db_url: str) -> Config:
    ini_path = "backend/alembic.ini" if os.path.exists("backend/alembic.ini") else "alembic.ini"
    cfg = Config(ini_path)
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    return cfg


def upgrade_control_plane_database() -> str:
    """Apply the control-plane history, adopting compatible unversioned schemas."""
    db_url = ensure_postgres_db_exists(settings.CONTROL_PLANE_DB_NAME)
    return upgrade_control_plane_database_url(db_url)


def upgrade_control_plane_database_url(
    db_url: str, *, allow_metadata_create: bool | None = None
) -> str:
    """Upgrade a control-plane URL; safely baseline compatible legacy schemas."""
    config_path = _BACKEND_DIR / "control_plane" / "alembic.ini"
    cfg = Config(str(config_path))
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    allow_metadata_create = (
        _development_schema_autocreate_enabled()
        if allow_metadata_create is None
        else allow_metadata_create
    )

    if not _migration_revisions_available(cfg):
        ensure_metadata_schema(
            db_url, ControlBase.metadata, allow_create=allow_metadata_create
        )
        return db_url

    scripts = ScriptDirectory.from_config(cfg)
    engine = create_engine(db_url)
    adopt_existing_schema = False
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            existing_tables = tables - {"alembic_version"}
            if "alembic_version" in tables:
                current_revision = MigrationContext.configure(
                    connection
                ).get_current_revision()
                if current_revision and scripts.get_revision(current_revision) is None:
                    diffs = compare_metadata(
                        MigrationContext.configure(
                            connection, opts={"compare_type": True}
                        ),
                        ControlBase.metadata,
                    )
                    if not allow_metadata_create or diffs:
                        raise RuntimeError(
                            f"Control-plane database revision {current_revision!r} "
                            "is not present in the checked-in migration history. "
                            "Restore its revisions or create a reviewed migration "
                            "bridge; automatic re-baselining is allowed only in "
                            "development when the schema exactly matches the ORM."
                        )
                    adopt_existing_schema = True
            if existing_tables and "alembic_version" not in tables:
                diffs = compare_metadata(
                    MigrationContext.configure(
                        connection, opts={"compare_type": True}
                    ),
                    ControlBase.metadata,
                )
                missing_table_names = {
                    diff[1].name for diff in diffs if diff[0] == "add_table"
                }
                safe_to_add_missing_tables = all(
                    diff[0] == "add_table"
                    or (
                        diff[0] == "add_index"
                        and diff[1].table.name in missing_table_names
                    )
                    for diff in diffs
                )
                if diffs and not safe_to_add_missing_tables:
                    raise RuntimeError(
                        "The existing control-plane database has no Alembic version "
                        "and differs from the current schema beyond missing tables. "
                        "Refusing to baseline it automatically; inspect the schema "
                        "and create a corrective migration."
                    )
                if diffs:
                    ControlBase.metadata.create_all(bind=connection, checkfirst=True)
                    connection.commit()
                    diffs = compare_metadata(
                        MigrationContext.configure(
                            connection, opts={"compare_type": True}
                        ),
                        ControlBase.metadata,
                    )
                if diffs:
                    raise RuntimeError(
                        "The unversioned control-plane schema could not be reconciled "
                        f"with ORM metadata: {diffs!r}"
                    )
                adopt_existing_schema = True
    finally:
        engine.dispose()

    if adopt_existing_schema:
        command.stamp(cfg, "head")
    else:
        command.upgrade(cfg, "head")

    schema_diffs = detect_schema_diff(db_url, ControlBase.metadata)
    if schema_diffs:
        raise RuntimeError(
            "Control-plane Alembic migrations completed, but the database still "
            "differs from the current ORM schema. Create and review a control-plane "
            f"migration before deploying. Remaining differences: {schema_diffs!r}"
        )
    return db_url


def upgrade_tenant_database_url(
    db_url: str,
    *,
    allow_metadata_create: bool | None = None,
    validate_schema: bool = True,
) -> None:
    """Upgrade a tenant database or safely recover a disposable dev schema."""
    cfg = _get_alembic_cfg(db_url)
    allow_metadata_create = (
        _development_schema_autocreate_enabled()
        if allow_metadata_create is None
        else allow_metadata_create
    )

    if not _migration_revisions_available(cfg):
        ensure_metadata_schema(db_url, Base.metadata, allow_create=allow_metadata_create)
        return

    scripts = ScriptDirectory.from_config(cfg)
    head = scripts.get_current_head()
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            existing_tables = tables - {"alembic_version"}
            has_version_table = "alembic_version" in tables
            current_revision = (
                MigrationContext.configure(connection).get_current_revision()
                if has_version_table
                else None
            )
            schema_diffs = compare_metadata(
                MigrationContext.configure(
                    connection, opts={"compare_type": True}
                ),
                Base.metadata,
            )
    finally:
        engine.dispose()

    if existing_tables and not has_version_table:
        if allow_metadata_create and not schema_diffs:
            command.stamp(cfg, "head")
        else:
            raise RuntimeError(
                "Tenant database has no Alembic version table. It can only be "
                "adopted automatically in development when it exactly matches "
                "the current ORM schema; inspect it and restore its migrations."
            )
    elif current_revision and scripts.get_revision(current_revision) is None:
        if allow_metadata_create and not schema_diffs:
            command.stamp(cfg, "head")
        else:
            raise RuntimeError(
                f"Tenant database revision {current_revision!r} is not present in "
                "the checked-in migration history. Restore the matching revisions "
                "or create a reviewed migration bridge."
            )

    if head is not None:
        command.upgrade(cfg, "head")

    if validate_schema:
        schema_diffs = detect_schema_diff(db_url, Base.metadata)
        if schema_diffs:
            raise RuntimeError(
                "Tenant Alembic migrations completed, but the database still differs "
                "from the current ORM schema. Create and review a tenant migration. "
                f"Remaining differences: {schema_diffs!r}"
            )


def get_current_db_revision(db_url: str) -> str:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            mc = MigrationContext.configure(conn)
            return mc.get_current_revision() or "NONE"
    finally:
        engine.dispose()


def validate_tenant_database_url(db_url: str) -> None:
    """Require a production tenant database to match the checked-in Alembic head."""
    cfg = _get_alembic_cfg(db_url)
    if not _migration_revisions_available(cfg):
        raise RuntimeError(
            "Tenant migration revisions are missing; production startup cannot "
            "verify the database schema."
        )

    head = ScriptDirectory.from_config(cfg).get_current_head()
    current = get_current_db_revision(db_url)
    if not head or current != head:
        raise RuntimeError(
            f"Tenant database revision {current!r} does not match checked-in "
            f"head {head!r}; run the approved canary/fleet migration before "
            "starting the new application release."
        )

    schema_diffs = detect_schema_diff(db_url, Base.metadata)
    if schema_diffs:
        raise RuntimeError(
            "Tenant database is at the Alembic head but differs from the current "
            "ORM metadata. Create and review a corrective migration before "
            f"starting the new application release: {schema_diffs!r}"
        )


def detect_schema_diff(db_url: str, metadata) -> list:
    """Compares live PostgreSQL schema against SQLAlchemy ORM metadata."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            mc = MigrationContext.configure(conn, opts={"compare_type": True})
            return compare_metadata(mc, metadata)
    finally:
        engine.dispose()


def upgrade_single_database(db_name: str) -> str:
    """Upgrades a single tenant database to Alembic head and syncs RBAC permissions."""
    db_url = ensure_postgres_db_exists(db_name)
    upgrade_tenant_database_url(db_url)

    eng = create_engine(db_url)
    try:
        from sqlalchemy.orm import sessionmaker
        TSession = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        with TSession() as db:
            ensure_permissions_exist(db)
            db.commit()
    finally:
        eng.dispose()

    return get_current_db_revision(db_url)


def migrate_default_canary(message: str | None = None) -> None:
    """
    STAGE 1: Migrates ONLY the configured default tenant (Canary DB).
    Generates a new revision if unversioned model changes are detected.
    """
    default_db_name = (
        f"{settings.TENANT_DATABASE_PREFIX}{settings.DEFAULT_TENANT_SLUG}"
    )
    print("\n========================================================")
    print(f"  STAGE 1: CANARY MIGRATION ({default_db_name} ONLY)")
    print("========================================================")

    upgrade_control_plane_database()

    default_db_url = ensure_postgres_db_exists(default_db_name)
    cfg = _get_alembic_cfg(default_db_url)
    upgrade_tenant_database_url(default_db_url, validate_schema=False)

    # 1. Apply any existing revisions to the canary database first.
    scripts = ScriptDirectory.from_config(cfg)
    if scripts.get_current_head() is not None:
        print(f"[*] Applying existing revisions to '{default_db_name}'...")
        command.upgrade(cfg, "head")

    # 2. Check for unversioned model changes in the canary database.
    diffs = detect_schema_diff(default_db_url, Base.metadata)
    if not diffs:
        rev = get_current_db_revision(default_db_url)
        print(f"[✓] '{default_db_name}' is already up-to-date at revision: {rev}")
    else:
        print(f"\n[!] Detected {len(diffs)} schema change(s) on '{default_db_name}':")
        for d in diffs[:10]:
            print(f"    -> {d}")
        if len(diffs) > 10:
            print(f"    ... and {len(diffs) - 10} more.")

        comment = message
        if not comment:
            # OPS-002: Prevent interactive hanging in Docker/CI environments
            print("[x] CRITICAL: Unversioned schema changes detected but no --message was provided.")
            print("    Run manually: docker compose run --rm api python backend/scripts/migrate.py -m 'your_comment'")
            sys.exit(1)

        print(f"[*] Generating Alembic revision: '{comment}'...")
        command.revision(cfg, message=comment, autogenerate=True)

        print(f"[*] Upgrading ONLY '{default_db_name}' to new head...")
        new_rev = upgrade_single_database(default_db_name)
        print(f"[✓] Canary database '{default_db_name}' upgraded to revision: {new_rev}")

    print("\n[i] Other hospital databases were NOT touched.")
    print(f"    Once you verify '{default_db_name}', push to the fleet with:")
    print("    docker compose exec api python backend/scripts/migrate.py --fleet\n")


def migrate_remaining_fleet(target_slug: str | None = None) -> dict:
    """
    STAGE 2: Pushes the verified Alembic head revision to all other active hospital databases
    (or a single specified --tenant <slug>).
    """
    print("\n========================================================")
    print("  STAGE 2: FLEET SCHEMA ROLLOUT (PUSH TO TENANT DBs)")
    print("========================================================")

    default_db_name = (
        f"{settings.TENANT_DATABASE_PREFIX}{settings.DEFAULT_TENANT_SLUG}"
    )
    default_db_url = ensure_postgres_db_exists(default_db_name)
    validate_tenant_database_url(default_db_url)
    print(
        f"[*] Verified canary '{default_db_name}' is at the checked-in head; "
        "continuing with the requested tenant rollout."
    )
    upgraded = []
    failed = []

    with ControlSessionLocal() as control_db:
        stmt = select(TenantRegistry).where(
            TenantRegistry.is_active == True,
            TenantRegistry.deployment_mode == DeploymentModeEnum.CLOUD,
        )
        if target_slug:
            stmt = stmt.where(TenantRegistry.subdomain_slug == target_slug.strip().lower())

        tenants = list(control_db.execute(stmt).scalars().all())
        for t in tenants:
            if t.db_name == default_db_name and not target_slug:
                continue
            try:
                print(f"[*] Pushing head migration to '{t.db_name}' (Tenant: {t.subdomain_slug})...")
                rev = upgrade_single_database(t.db_name)
                upgraded.append({"subdomain_slug": t.subdomain_slug, "db_name": t.db_name, "revision": rev})
                print(f"    [✓] '{t.db_name}' -> {rev}")
            except Exception as exc:
                failed.append({"subdomain_slug": t.subdomain_slug, "db_name": t.db_name, "error": str(exc)})
                print(f"    [x] FAILED on '{t.db_name}': {exc}")

        if upgraded or failed:
            control_db.add(
                ControlPlaneAuditLog(
                    action="UPGRADE_FLEET_SCHEMA",
                    target_slug=target_slug or "ALL_FLEET",
                    details=f"Upgraded {len(upgraded)} database(s) to head; {len(failed)} failed.",
                )
            )
            control_db.commit()

    if failed:
        print(
            f"\n[!] Rollout incomplete: {len(upgraded)} upgraded, "
            f"{len(failed)} failed.\n"
        )
    else:
        print(f"\n[✓] Rollout complete: {len(upgraded)} upgraded, 0 failed.\n")
    return {"upgraded": upgraded, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Avinya HMS Staged Alembic Migration Manager")
    parser.add_argument(
        "--default-only",
        action="store_true",
        help="Stage 1: Migrate only the configured default tenant as canary",
    )
    parser.add_argument("--fleet", action="store_true", help="Stage 2: Push verified head revision to all tenant DBs")
    parser.add_argument("--tenant", type=str, default=None, help="Push head revision to one specific tenant slug")
    parser.add_argument("-m", "--message", type=str, default=None, help="Revision comment (non-interactive)")
    args = parser.parse_args(argv)

    if args.fleet or args.tenant:
        result = migrate_remaining_fleet(target_slug=args.tenant)
        return 1 if result["failed"] else 0
    else:
        migrate_default_canary(message=args.message)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())