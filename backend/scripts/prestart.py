import logging
import os
import secrets
import sys
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from config import settings
from control_models import ControlBase, DeploymentModeEnum, TenantRegistry
from database import ControlSessionLocal, build_db_url, control_engine, ensure_postgres_db_exists
from seed import ensure_permissions_exist, seed_universal_hospital_data

from security.envelope import encrypt_tenant_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.prestart")

MIGRATION_LOCK_ID = 849204719


def migrate_single_tenant_db(db_name: str) -> None:
    target_url = ensure_postgres_db_exists(db_name)
    os.environ["DATABASE_URL"] = target_url
    alembic_ini_path = "backend/alembic.ini" if os.path.exists("backend/alembic.ini") else "alembic.ini"
    alembic_cfg = Config(alembic_ini_path)
    alembic_cfg.set_main_option("sqlalchemy.url", target_url)
    command.upgrade(alembic_cfg, "head")

    t_engine = create_engine(target_url)
    TSession = sessionmaker(bind=t_engine, autocommit=False, autoflush=False)
    with TSession() as db:
        ensure_permissions_exist(db)
        db.commit()
    t_engine.dispose()


def run_prestart() -> None:
    ensure_postgres_db_exists(settings.CONTROL_PLANE_DB_NAME)

    with control_engine.connect() as conn:
        conn.execute(text(f"SELECT pg_advisory_lock({MIGRATION_LOCK_ID})"))
        conn.commit()
        try:
            logger.info("Initializing Control Plane schema...")
            ControlBase.metadata.create_all(bind=control_engine)

            with ControlSessionLocal() as control_db:
                default_tenant = (
                    control_db.query(TenantRegistry)
                    .filter(TenantRegistry.subdomain_slug == settings.DEFAULT_TENANT_SLUG)
                    .first()
                )
                if not default_tenant:
                    default_db = f"hms_{settings.DEFAULT_TENANT_SLUG}"
                    default_tenant = TenantRegistry(
                        hospital_name=settings.DEFAULT_HOSPITAL_NAME,
                        branch_code=settings.DEFAULT_BRANCH_CODE,
                        subdomain_slug=settings.DEFAULT_TENANT_SLUG,
                        db_name=default_db,
                        admin_email=settings.SEED_ADMIN_EMAIL,
                        jwt_secret_key=encrypt_tenant_secret(settings.SECRET_KEY),
                        backup_encryption_key=encrypt_tenant_secret(secrets.token_urlsafe(32)),
                        deployment_mode=DeploymentModeEnum.CLOUD,
                        is_active=True,
                    )
                    control_db.add(default_tenant)
                    control_db.commit()

                stmt = select(TenantRegistry).where(
                    TenantRegistry.is_active == True,
                    TenantRegistry.deployment_mode == DeploymentModeEnum.CLOUD,
                )
                tenants = control_db.execute(stmt).scalars().all()

                for tenant in tenants:
                    try:
                        logger.info("Auto-migrating hospital tenant '%s' (%s)...", tenant.subdomain_slug, tenant.db_name)
                        migrate_single_tenant_db(tenant.db_name)
                        if tenant.subdomain_slug == settings.DEFAULT_TENANT_SLUG:
                            t_engine = create_engine(build_db_url(tenant.db_name))
                            TSession = sessionmaker(bind=t_engine)
                            with TSession() as t_db:
                                seed_universal_hospital_data(
                                    db=t_db,
                                    hospital_name=tenant.hospital_name,
                                    branch_code=tenant.branch_code,
                                    admin_email=tenant.admin_email,
                                    admin_password=settings.SEED_ADMIN_PASSWORD,
                                )
                            t_engine.dispose()
                    except Exception as tenant_exc:
                        logger.error("Migration failed for tenant %s: %s", tenant.subdomain_slug, str(tenant_exc))
        except Exception as exc:
            logger.error("Fatal prestart error: %s", str(exc))
            sys.exit(1)
        finally:
            conn.execute(text(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_ID})"))
            conn.commit()


if __name__ == "__main__":
    run_prestart()