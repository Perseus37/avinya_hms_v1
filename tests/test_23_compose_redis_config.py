from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REDIS_PASSWORD_REFERENCE = "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"
REDIS_CONTAINER_PASSWORD_REFERENCE = (
    "$${REDIS_PASSWORD:?REDIS_PASSWORD is required}"
)


def test_redis_password_is_required_and_healthcheck_authenticates():
    for compose_file in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = yaml.safe_load((REPOSITORY_ROOT / compose_file).read_text())
        redis = compose["services"]["redis"]

        assert redis.get("env_file") is None
        assert redis["environment"]["REDIS_PASSWORD"] == REDIS_PASSWORD_REFERENCE
        assert REDIS_CONTAINER_PASSWORD_REFERENCE in " ".join(redis["command"])
        assert redis["healthcheck"]["test"][0] == "CMD-SHELL"
        assert "REDISCLI_AUTH=$$REDIS_PASSWORD" in redis["healthcheck"]["test"][1]
        assert "redis-cli -p $$REDIS_PORT ping" in redis["healthcheck"]["test"][1]
        assert "REDIS_PORT" in redis["environment"]
        assert "--port" in " ".join(redis["command"])


def test_compose_runs_database_bootstrap_once_before_api():
    for compose_file in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = yaml.safe_load((REPOSITORY_ROOT / compose_file).read_text())
        db_init = compose["services"]["db-init"]
        api = compose["services"]["api"]

        assert db_init["restart"] == "no"
        assert db_init["command"] in (
            "python backend/scripts/bootstrap.py",
            ["python", "backend/scripts/bootstrap.py"],
        )
        assert (
            api["depends_on"]["db-init"]["condition"]
            == "service_completed_successfully"
        )
        assert api["command"][0] == "gunicorn"


def test_schema_autocreate_is_enabled_only_for_local_compose():
    local = yaml.safe_load((REPOSITORY_ROOT / "docker-compose.yml").read_text())
    production = yaml.safe_load(
        (REPOSITORY_ROOT / "docker-compose.prod.yml").read_text()
    )

    def env_value(env_path: str, key: str) -> str:
        for line in (REPOSITORY_ROOT / env_path).read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
        raise AssertionError(f"{key} is missing from {env_path}")

    assert env_value(".env.example", "DEVELOPMENT_AUTO_CREATE_SCHEMA") == "true"
    assert (
        env_value(".env.production.example", "DEVELOPMENT_AUTO_CREATE_SCHEMA")
        == "false"
    )
    for service_name in ("db-init", "api", "onboarding-portal"):
        assert local["services"][service_name]["env_file"] == ".env"
        assert production["services"][service_name]["env_file"] == ".env.production"


def test_environment_templates_define_all_required_settings():
    from backend.core.config import Settings

    required_settings = {
        name
        for name, field in Settings.model_fields.items()
        if field.is_required()
    }
    for template in (".env.example", ".env.production.example"):
        configured = {
            line.split("=", 1)[0]
            for line in (REPOSITORY_ROOT / template).read_text().splitlines()
            if line and not line.startswith("#") and "=" in line
        }
        assert required_settings <= configured


def test_backup_timing_and_storage_paths_are_environment_configured():
    local = yaml.safe_load((REPOSITORY_ROOT / "docker-compose.yml").read_text())
    backup_command = local["services"]["backup-sidecar"]["command"]
    assert any("BACKUP_INTERVAL_SECONDS" in part for part in backup_command)
    assert any("BACKUP_OUTPUT_DIR" in part for part in backup_command)
    assert (
        "VAULT_POLL_INTERVAL_SECONDS"
        in local["services"]["backup-vault"]["environment"]
    )


def test_production_backup_and_attachment_paths_are_host_mounts():
    production = yaml.safe_load(
        (REPOSITORY_ROOT / "docker-compose.prod.yml").read_text()
    )
    api_volumes = production["services"]["api"]["volumes"]

    assert "hms_backups:/app/backups" in api_volumes
    assert any("ATTACHMENTS_HOST_PATH" in volume for volume in api_volumes)
    assert "backup-sidecar" not in production["services"]
    production_env = (REPOSITORY_ROOT / ".env.production.example").read_text()
    assert "BACKUP_STORAGE_MOUNTPOINT=/mnt/avinya-data" in production_env
    assert "BACKUP_HOST_PATH=/mnt/avinya-data/backups" in production_env
    assert "ATTACHMENTS_HOST_PATH=/mnt/avinya-data/attachments" in production_env
    assert "hms_backups" in production["volumes"]
