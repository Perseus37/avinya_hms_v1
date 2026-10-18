from logging.config import fileConfig
from pathlib import Path
import sys
import os

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
for path in (REPO_DIR,):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.control_plane.models import ControlBase
from backend.core.config import settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = ControlBase.metadata

def get_database_url() -> str:
    # Programmatic callers target individual databases through this config.
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return configured_url

    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url

    return make_url(settings.DATABASE_URL).set(
        database=settings.CONTROL_PLANE_DB_NAME
    ).render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    url = get_database_url()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config.set_main_option(
        "sqlalchemy.url",
        get_database_url().replace("%", "%%"),
    )
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
