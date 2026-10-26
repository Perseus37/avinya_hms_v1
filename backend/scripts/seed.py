"""Seed synthetic clinical and hospital master data into a specified tenant database."""

import argparse
import logging
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BACKEND_DIR.parent
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.core.config import settings
from backend.core.database import ControlSessionLocal, build_db_url
from backend.control_plane.models import TenantRegistry
from backend.core.seed import ensure_permissions_exist, seed_universal_hospital_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("avinya.seed")


def seed_tenant(slug: str | None = None) -> None:
    target_slug = (slug or settings.DEFAULT_TENANT_SLUG).strip()
    with ControlSessionLocal() as control_db:
        tenant = control_db.execute(
            select(TenantRegistry).where(TenantRegistry.subdomain_slug == target_slug)
        ).scalar_one_or_none()

        if not tenant:
            db_name = f"{settings.TENANT_DATABASE_PREFIX}{target_slug}"
            hospital_name = settings.DEFAULT_HOSPITAL_NAME
            branch_code = settings.DEFAULT_BRANCH_CODE
            admin_email = settings.SEED_ADMIN_EMAIL
        else:
            db_name = tenant.db_name
            hospital_name = tenant.hospital_name
            branch_code = tenant.branch_code
            admin_email = tenant.admin_email

    t_url = build_db_url(db_name)
    logger.info(f"Connecting to tenant database '{db_name}'...")
    t_engine = create_engine(t_url)
    TSession = sessionmaker(bind=t_engine)
    with TSession() as t_db:
        logger.info(f"Seeding master permissions for tenant '{target_slug}'...")
        ensure_permissions_exist(t_db)
        t_db.commit()

        logger.info(f"Seeding universal clinical and hospital data for '{hospital_name}'...")
        seed_universal_hospital_data(
            db=t_db,
            hospital_name=hospital_name,
            branch_code=branch_code,
            admin_email=admin_email,
            admin_password=settings.SEED_ADMIN_PASSWORD,
        )
    t_engine.dispose()
    logger.info(f"Successfully seeded tenant '{target_slug}'.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant",
        default=settings.DEFAULT_TENANT_SLUG,
        help="Subdomain slug of tenant to seed (default: default)",
    )
    args = parser.parse_args(argv)
    seed_tenant(args.tenant)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

