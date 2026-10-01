import logging
import sys
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from database import engine
from seed import seed_enterprise_data

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.prestart")

# Arbitrary constant 64-bit integer used as the cluster-wide migration lock ID
MIGRATION_LOCK_ID = 849204719


def run_prestart() -> None:
    """Acquires an exclusive PostgreSQL advisory lock, runs migrations, and seeds initial data."""
    logger.info("Connecting to PostgreSQL and acquiring exclusive migration lock...")
    
    with engine.connect() as conn:
        # Blocks other scaled API containers until the first container finishes
        conn.execute(text(f"SELECT pg_advisory_lock({MIGRATION_LOCK_ID})"))
        conn.commit()
        try:
            logger.info("Lock acquired. Running Alembic schema migrations...")
            alembic_cfg = Config("alembic.ini")
            command.upgrade(alembic_cfg, "head")
            logger.info("Alembic migrations are up to date.")

            logger.info("Verifying baseline seed data...")
            seed_enterprise_data()
            logger.info("Prestart initialization complete.")
        except Exception as exc:
            logger.error("Prestart failed: %s", str(exc))
            sys.exit(1)
        finally:
            # Always release the advisory lock so waiting containers can proceed
            conn.execute(text(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_ID})"))
            conn.commit()
            logger.info("Migration lock released.")


if __name__ == "__main__":
    run_prestart()