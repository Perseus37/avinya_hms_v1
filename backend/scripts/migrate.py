import argparse
import os
from pathlib import Path
import sys

_BACKEND_DIR = Path(__file__).resolve().parents[1]
for p in (_BACKEND_DIR, _BACKEND_DIR / "core", _BACKEND_DIR / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select

from config import settings
from control_models import ControlBase, ControlPlaneAuditLog, DeploymentModeEnum, TenantRegistry
from database import Base, ControlSessionLocal, ensure_postgres_db_exists
from seed import ensure_permissions_exist
import models  # noqa: F401


def _get_alembic_cfg(db_url: str) -> Config:
    ini_path = "backend/alembic.ini" if os.path.exists("backend/alembic.ini") else "alembic.ini"
    cfg = Config(ini_path)
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def get_current_db_revision(db_url: str) -> str:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            mc = MigrationContext.configure(conn)
            return mc.get_current_revision() or "NONE"
    finally:
        engine.dispose()


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
    cfg = _get_alembic_cfg(db_url)
    command.upgrade(cfg, "head")

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
    STAGE 1: Migrates ONLY hms_default (Canary DB).
    Generates a new revision if unversioned model changes are detected.
    """
    print("\n========================================================")
    print("  STAGE 1: CANARY MIGRATION (hms_default ONLY)")
    print("========================================================")

    cp_url = ensure_postgres_db_exists(settings.CONTROL_PLANE_DB_NAME)
    ControlBase.metadata.create_all(bind=create_engine(cp_url))

    default_db_name = f"hms_{settings.DEFAULT_TENANT_SLUG}"
    default_db_url = ensure_postgres_db_exists(default_db_name)
    cfg = _get_alembic_cfg(default_db_url)

    # 1. Apply any existing revisions to hms_default first
    scripts = ScriptDirectory.from_config(cfg)
    if scripts.get_current_head() is not None:
        print(f"[*] Applying existing revisions to '{default_db_name}'...")
        command.upgrade(cfg, "head")

    # 2. Check if models.py has unversioned changes compared to hms_default
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

        comment = message or input("\n[?] Enter migration comment for new revision (or press Enter to abort): ").strip()
        if not comment:
            print("[x] Aborted. No revision generated.")
            sys.exit(0)

        print(f"[*] Generating Alembic revision: '{comment}'...")
        command.revision(cfg, message=comment, autogenerate=True)

        print(f"[*] Upgrading ONLY '{default_db_name}' to new head...")
        new_rev = upgrade_single_database(default_db_name)
        print(f"[✓] Canary database '{default_db_name}' upgraded to revision: {new_rev}")

    print("\n[i] Other hospital databases were NOT touched.")
    print("    Once you verify 'hms_default', push to the rest of the fleet with:")
    print("    docker compose exec api python backend/scripts/migrate.py --fleet\n")


def migrate_remaining_fleet(target_slug: str | None = None) -> dict:
    """
    STAGE 2: Pushes the verified Alembic head revision to all other active hospital databases
    (or a single specified --tenant <slug>).
    """
    print("\n========================================================")
    print("  STAGE 2: FLEET SCHEMA ROLLOUT (PUSH TO TENANT DBs)")
    print("========================================================")

    default_db_name = f"hms_{settings.DEFAULT_TENANT_SLUG}"
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

        if upgraded:
            control_db.add(
                ControlPlaneAuditLog(
                    action="UPGRADE_FLEET_SCHEMA",
                    target_slug=target_slug or "ALL_FLEET",
                    details=f"Upgraded {len(upgraded)} database(s) to head; {len(failed)} failed.",
                )
            )
            control_db.commit()

    print(f"\n[✓] Rollout complete: {len(upgraded)} upgraded, {len(failed)} failed.\n")
    return {"upgraded": upgraded, "failed": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Avinya HMS Staged Alembic Migration Manager")
    parser.add_argument("--default-only", action="store_true", help="Stage 1: Migrate ONLY hms_default (Canary)")
    parser.add_argument("--fleet", action="store_true", help="Stage 2: Push verified head revision to all tenant DBs")
    parser.add_argument("--tenant", type=str, default=None, help="Push head revision to one specific tenant slug")
    parser.add_argument("-m", "--message", type=str, default=None, help="Revision comment (non-interactive)")
    args = parser.parse_args()

    if args.fleet or args.tenant:
        migrate_remaining_fleet(target_slug=args.tenant)
    else:
        migrate_default_canary(message=args.message)