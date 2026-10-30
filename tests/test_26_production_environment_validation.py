from pathlib import Path

import pytest

from backend.scripts.validate_production_env import (
    REQUIRED_PRODUCTION_SECRETS,
    main,
    validate_production_env,
)


def _valid_env_values() -> dict[str, str]:
    values = {
        name: f"distinct-production-value-{index}-long-enough"
        for index, name in enumerate(REQUIRED_PRODUCTION_SECRETS)
    }
    values.update(
        CONTROL_PLANE_LEGACY_MASTER_KEY_ENABLED="false",
        TRUSTED_PROXY_IPS="10.20.0.1/32,10.30.0.0/16",
    )
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    if path.stat().st_mode and hasattr(path, "chmod"):
        path.chmod(0o600)


def test_production_env_accepts_distinct_non_placeholder_secrets(tmp_path):
    env_file = tmp_path / ".env.production"
    _write_env_file(env_file, _valid_env_values())

    assert validate_production_env(env_file) == []


def test_production_env_rejects_missing_empty_placeholder_and_reused_secrets(
    tmp_path,
):
    values = _valid_env_values()
    values["REDIS_PASSWORD"] = values["POSTGRES_PASSWORD"]
    values["SECRET_KEY"] = "REPLACE_WITH_SECRET"
    values["MASTER_ONBOARDING_KEY"] = ""
    values["CONTROL_PLANE_LEGACY_MASTER_KEY_ENABLED"] = "true"
    values.pop("CONTROL_PLANE_MASTER_KEY", None)
    env_file = tmp_path / ".env.production"
    _write_env_file(env_file, values)

    errors = validate_production_env(env_file)

    assert any("REDIS_PASSWORD" in error for error in errors)
    assert any("SECRET_KEY" in error for error in errors)
    assert any("MASTER_ONBOARDING_KEY" in error for error in errors)
    assert any("CONTROL_PLANE_MASTER_KEY" in error for error in errors)
    assert any("must be false in production" in error for error in errors)
    assert any("must be unique" in error for error in errors)
    assert "REPLACE_WITH_SECRET" not in "\n".join(errors)


def test_production_env_rejects_weak_secret_key(tmp_path):
    values = _valid_env_values()
    values["SECRET_KEY"] = "short"
    env_file = tmp_path / ".env.production"
    _write_env_file(env_file, values)

    assert any(
        "at least 32 characters" in error
        for error in validate_production_env(env_file)
    )


@pytest.mark.parametrize("proxy_value", ["not-a-cidr", "0.0.0.0/0", "::/0"])
def test_production_env_requires_specific_trusted_proxy_cidrs(
    tmp_path, proxy_value
):
    values = _valid_env_values()
    values["TRUSTED_PROXY_IPS"] = proxy_value
    env_file = tmp_path / ".env.production"
    _write_env_file(env_file, values)

    errors = validate_production_env(env_file)
    assert any("TRUSTED_PROXY_IPS" in error for error in errors)


def test_production_env_cli_fails_for_missing_file(tmp_path, capsys):
    exit_code = main(["--env-file", str(tmp_path / "missing.env")])

    assert exit_code == 1
    assert "does not exist" in capsys.readouterr().out


def test_wider_production_configuration_validation(tmp_path):
    """Verifies that validate_production_env validates safety flags, FQDNs, CORS, and leftover template placeholders."""
    values = _valid_env_values()
    values.update(
        ENVIRONMENT="invalid_env",
        DEVELOPMENT_AUTO_CREATE_SCHEMA="true",
        PRIVACY_ALLOW_RETENTION_EXECUTE="true",
        BASE_DOMAIN="localhost",
        CORS_ORIGINS="*",
        SEED_ADMIN_EMAIL="admin_without_at",
        POSTGRES_USER="REPLACE_WITH_USER",
    )
    env_file = tmp_path / ".env.production"
    _write_env_file(env_file, values)

    errors = validate_production_env(env_file)
    assert any("ENVIRONMENT" in error for error in errors)
    assert any("DEVELOPMENT_AUTO_CREATE_SCHEMA" in error for error in errors)
    assert any("PRIVACY_ALLOW_RETENTION_EXECUTE" in error for error in errors)
    assert any("BASE_DOMAIN" in error for error in errors)
    assert any("CORS_ORIGINS" in error for error in errors)
    assert any("SEED_ADMIN_EMAIL" in error for error in errors)
    assert any("POSTGRES_USER" in error for error in errors)

def test_production_env_rejects_known_default_salt_and_weak_salts(tmp_path):
    values = _valid_env_values()
    values["ENVELOPE_ENCRYPTION_SALT"] = "avinya_secure_salt_2026"
    values["BACKUP_ENCRYPTION_SALT"] = "too-short"
    env_file = tmp_path / ".env.production"
    _write_env_file(env_file, values)

    errors = validate_production_env(env_file)
    assert any("ENVELOPE_ENCRYPTION_SALT must not use known default salt" in error for error in errors)
    assert any("BACKUP_ENCRYPTION_SALT must contain at least 16 characters" in error for error in errors)


def test_production_env_enforces_token_lifetime_limits(tmp_path):
    values = _valid_env_values()
    values["ACCESS_TOKEN_EXPIRE_MINUTES"] = "120"
    values["CONTROL_PLANE_ACCESS_TOKEN_MINUTES"] = "0"
    env_file = tmp_path / ".env.production"
    _write_env_file(env_file, values)

    errors = validate_production_env(env_file)
    assert any("ACCESS_TOKEN_EXPIRE_MINUTES must be between 1 and 60 minutes" in error for error in errors)
    assert any("CONTROL_PLANE_ACCESS_TOKEN_MINUTES must be between 1 and 60 minutes" in error for error in errors)


def test_production_env_rejects_disabled_database_tls(tmp_path):
    values = _valid_env_values()
    values["POSTGRES_SSLMODE"] = "disable"
    env_file = tmp_path / ".env.production"
    _write_env_file(env_file, values)

    errors = validate_production_env(env_file)
    assert any("POSTGRES_SSLMODE must not disable TLS in production" in error for error in errors)


def test_production_env_requires_secure_metrics_scrape_token(tmp_path):
    values = _valid_env_values()
    values["METRICS_SCRAPE_TOKEN"] = "short"
    env_file = tmp_path / ".env.production"
    _write_env_file(env_file, values)

    errors = validate_production_env(env_file)
    assert any("METRICS_SCRAPE_TOKEN must contain at least 16 characters" in error for error in errors)

