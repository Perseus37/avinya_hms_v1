from logging.config import fileConfig
from pathlib import Path
import sys , os

from alembic import context
from sqlalchemy import create_engine, pool

# Resolve the repository package namespace for Alembic subprocesses.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BACKEND_DIR.parent
for p in (_REPO_DIR,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backend.core.config import settings
from backend.core.database import Base
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

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL") or settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL") or settings.DATABASE_URL
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()