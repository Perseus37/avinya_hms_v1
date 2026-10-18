import os
import secrets
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES17_INTEGRATION") != "1"
    or not os.getenv("POSTGRES17_TEST_URL"),
    reason="Set RUN_POSTGRES17_INTEGRATION=1 and POSTGRES17_TEST_URL for a disposable PostgreSQL 17 instance.",
)


def test_postgresql_17_migrations_match_control_and_tenant_metadata():
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect
    from alembic.autogenerate import compare_metadata

    from backend.control_plane.models import ControlBase
    from backend.core.database import Base
    from backend.scripts.migrate import (
        upgrade_control_plane_database_url,
        upgrade_tenant_database_url,
    )

    admin_url = make_url(os.environ["POSTGRES17_TEST_URL"])
    admin_engine = create_engine(admin_url)
    suffix = uuid.uuid4().hex[:10]
    db_names = (f"test_cp_{suffix}", f"test_tenant_{suffix}")
    database_urls = [
        admin_url.set(database=name).render_as_string(hide_password=False)
        for name in db_names
    ]
    try:
        with admin_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            for name in db_names:
                connection.execute(text(f'CREATE DATABASE "{name}"'))

        upgrade_control_plane_database_url(database_urls[0])
        upgrade_tenant_database_url(database_urls[1])

        for database_url, metadata in (
            (database_urls[0], ControlBase.metadata),
            (database_urls[1], Base.metadata),
        ):
            engine = create_engine(database_url)
            try:
                with engine.connect() as connection:
                    differences = compare_metadata(
                        MigrationContext.configure(
                            connection, opts={"compare_type": True}
                        ),
                        metadata,
                    )
                    assert set(inspect(connection).get_table_names()) - {
                        "alembic_version"
                    } == set(metadata.tables)
                    assert differences == []
            finally:
                engine.dispose()
    finally:
        with admin_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            for name in db_names:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :db_name AND pid <> pg_backend_pid()"
                    ),
                    {"db_name": name},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin_engine.dispose()


@pytest.mark.skipif(
    not os.getenv("POSTGRES17_DOCKER_CONTAINER"),
    reason="Set POSTGRES17_DOCKER_CONTAINER to exercise pg_dump/GPG/pg_restore round-trip.",
)
def test_postgresql_17_encrypted_custom_dump_restores_into_isolated_database():
    admin_url = make_url(os.environ["POSTGRES17_TEST_URL"])
    admin_engine = create_engine(admin_url)
    suffix = uuid.uuid4().hex[:10]
    source_name = f"test_backup_{suffix}"
    target_name = f"test_restore_{suffix}"
    container = os.environ["POSTGRES17_DOCKER_CONTAINER"]
    container_port = os.getenv("POSTGRES17_CONTAINER_PORT", "5432")
    passphrase = secrets.token_urlsafe(32)
    created_databases = []

    def docker_exec(*args, input_bytes=None):
        return subprocess.run(
            ["docker", "exec", *args],
            input=input_bytes,
            capture_output=True,
            check=True,
        )

    try:
        with admin_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            for database_name in (source_name, target_name):
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
                created_databases.append(database_name)

        source_engine = create_engine(
            admin_url.set(database=source_name).render_as_string(
                hide_password=False
            )
        )
        try:
            with source_engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE TABLE backup_probe "
                        "(id integer PRIMARY KEY, value text NOT NULL)"
                    )
                )
                connection.execute(
                    text("INSERT INTO backup_probe (id, value) VALUES (1, 'ok')")
                )
        finally:
            source_engine.dispose()

        pg_dump = docker_exec(
            "-e",
            f"PGPASSWORD={admin_url.password or ''}",
            container,
            "pg_dump",
            "-h",
            "127.0.0.1",
            "-p",
            container_port,
            "-U",
            admin_url.username or "postgres",
            "--format=custom",
            "--no-owner",
            "--no-acl",
            source_name,
        ).stdout
        assert pg_dump

        encrypt = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--interactive",
                "--env",
                f"TEST_GPG_PASSPHRASE={passphrase}",
                "alpine:3.21",
                "sh",
                "-ec",
                "apk add --no-cache gnupg >/dev/null; "
                "printf '%s' \"$TEST_GPG_PASSPHRASE\" > /tmp/pass; "
                "gpg --batch --quiet --yes --pinentry-mode loopback "
                "--symmetric --cipher-algo AES256 "
                "--passphrase-file /tmp/pass --output /tmp/backup.gpg; "
                "cat /tmp/backup.gpg",
            ],
            input=pg_dump,
            capture_output=True,
            check=True,
        ).stdout

        decrypt = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--interactive",
                "--env",
                f"TEST_GPG_PASSPHRASE={passphrase}",
                "alpine:3.21",
                "sh",
                "-ec",
                "apk add --no-cache gnupg >/dev/null; "
                "printf '%s' \"$TEST_GPG_PASSPHRASE\" > /tmp/pass; "
                "cat > /tmp/backup.gpg; "
                "gpg --batch --quiet --pinentry-mode loopback --decrypt "
                "--passphrase-file /tmp/pass /tmp/backup.gpg",
            ],
            input=encrypt,
            capture_output=True,
            check=True,
        ).stdout
        assert decrypt == pg_dump

        docker_exec(
            "--interactive",
            "-e",
            f"PGPASSWORD={admin_url.password or ''}",
            container,
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            "-h",
            "127.0.0.1",
            "-p",
            container_port,
            "-U",
            admin_url.username or "postgres",
            "--dbname",
            target_name,
            input_bytes=decrypt,
        )
        target_engine = create_engine(
            admin_url.set(database=target_name).render_as_string(
                hide_password=False
            )
        )
        try:
            with target_engine.connect() as connection:
                restored = connection.execute(
                    text("SELECT id, value FROM backup_probe")
                ).one()
            assert restored == (1, "ok")
        finally:
            target_engine.dispose()
    finally:
        with admin_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            for database_name in created_databases:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :db_name AND pid <> pg_backend_pid()"
                    ),
                    {"db_name": database_name},
                )
                connection.execute(
                    text(f'DROP DATABASE IF EXISTS "{database_name}"')
                )
        admin_engine.dispose()
