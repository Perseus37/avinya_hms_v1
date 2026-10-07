from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool,create_engine

# Resolve backend/, backend/core/, and backend/scripts/
_BACKEND_DIR = Path(__file__).resolve().parents[1]
for p in (_BACKEND_DIR, _BACKEND_DIR / "core", _BACKEND_DIR / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import settings
from control_models import ControlBase
from database import Base
import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_target_metadata(db_url: str):
    """Routes Alembic autogenerate to ControlBase for hms_control_plane, and Base for tenant DBs."""
    if settings.CONTROL_PLANE_DB_NAME in db_url:
        return ControlBase.metadata
    return Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=get_target_metadata(url),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or settings.DATABASE_URL
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=get_target_metadata(url),
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()