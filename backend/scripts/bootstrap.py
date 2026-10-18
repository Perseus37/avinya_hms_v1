import logging
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BACKEND_DIR.parent
for _path in (_REPO_DIR,):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.core.config import settings
from backend.control_plane.models import DeploymentModeEnum, TenantRegistry
from backend.core.database import ControlSessionLocal, build_db_url, control_engine
from backend.scripts.migrate import (
    upgrade_control_plane_database_url,
    upgrade_tenant_database_url,
    validate_tenant_database_url,
)
from backend.security.envelope import encrypt_tenant_secret
from backend.core.seed import ensure_permissions_exist, seed_universal_hospital_data
from backend.control_plane.services.catalogs import seed_control_plane

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.bootstrap")


def provision_physical_databases():
    """Acts as local Infrastructure-as-Code to ensure databases physically exist."""
    conn = psycopg2.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database="postgres",
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        default_tenant = (
            f"{settings.TENANT_DATABASE_PREFIX}{settings.DEFAULT_TENANT_SLUG}"
        )
        for db_name in [settings.CONTROL_PLANE_DB_NAME, default_tenant]:
            cur.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (db_name,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{db_name}"')
                logger.info(f"Provisioned physical database: {db_name}")
    conn.close()


def bootstrap_system():
    # 1. Ensure databases physically exist before ORM loads
    provision_physical_databases()

    default_tenant_db = (
        f"{settings.TENANT_DATABASE_PREFIX}{settings.DEFAULT_TENANT_SLUG}"
    )
    cp_url = build_db_url(settings.CONTROL_PLANE_DB_NAME)

    logger.info("--- Upgrading Control Plane Schema ---")
    upgrade_control_plane_database_url(cp_url)

    with ControlSessionLocal() as control_db:
        # Seed Control Plane
        logger.info("Seeding Control Plane Data...")
        seed_control_plane(control_db)

        # Ensure Default Tenant exists in Registry
        existing_default = control_db.execute(
            select(TenantRegistry).where(TenantRegistry.subdomain_slug == settings.DEFAULT_TENANT_SLUG)
        ).scalar_one_or_none()

        default_tenant_created = existing_default is None
        if default_tenant_created:
            import secrets
            control_db.add(TenantRegistry(
                hospital_name=settings.DEFAULT_HOSPITAL_NAME,
                branch_code=settings.DEFAULT_BRANCH_CODE,
                subdomain_slug=settings.DEFAULT_TENANT_SLUG,
                db_name=default_tenant_db,
                admin_email=settings.SEED_ADMIN_EMAIL,
                jwt_secret_key=encrypt_tenant_secret(settings.SECRET_KEY),
                backup_encryption_key=encrypt_tenant_secret(secrets.token_urlsafe(32)),
                deployment_mode=DeploymentModeEnum.CLOUD,
                is_active=True,
            ))
            control_db.commit()

        development = settings.ENVIRONMENT.lower() in {
            "development",
            "dev",
            "testing",
        }
        active_tenants = control_db.execute(
            select(TenantRegistry).where(
                TenantRegistry.is_active == True,
                TenantRegistry.deployment_mode == DeploymentModeEnum.CLOUD,
            )
        ).scalars().all()
        if development:
            tenants = active_tenants
        elif default_tenant_created:
            default_tenant = next(
                (
                    tenant
                    for tenant in active_tenants
                    if tenant.subdomain_slug == settings.DEFAULT_TENANT_SLUG
                ),
                None,
            )
            if default_tenant is None:
                raise RuntimeError(
                    "The newly created default tenant is missing from the active "
                    "cloud tenant registry."
                )
            tenants = [default_tenant]
        else:
            tenants = []
            logger.info(
                "Skipping existing tenant schema upgrades during production "
                "startup; use the canary/fleet migration command after approval."
            )

        logger.info("--- Upgrading Tenant Schemas ---")
        for tenant in tenants:
            logger.info(f"Upgrading tenant: {tenant.subdomain_slug}")
            t_url = build_db_url(tenant.db_name)
            upgrade_tenant_database_url(t_url)

            # Seed specific tenant data
            t_engine = create_engine(t_url)
            TSession = sessionmaker(bind=t_engine)
            with TSession() as t_db:
                ensure_permissions_exist(t_db)
                t_db.commit()

                if tenant.subdomain_slug == settings.DEFAULT_TENANT_SLUG:
                    seed_universal_hospital_data(
                        db=t_db,
                        hospital_name=tenant.hospital_name,
                        branch_code=tenant.branch_code,
                        admin_email=tenant.admin_email,
                        admin_password=settings.SEED_ADMIN_PASSWORD,
                    )
            t_engine.dispose()

        if not development:
            logger.info("--- Verifying Existing Production Tenant Schemas ---")
            for tenant in active_tenants:
                validate_tenant_database_url(build_db_url(tenant.db_name))

    logger.info("Bootstrap complete.")


if __name__ == "__main__":
    bootstrap_system()