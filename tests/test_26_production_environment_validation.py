from pathlib import Path

from backend.scripts.validate_production_env import (
    REQUIRED_PRODUCTION_SECRETS,
    main,
    validate_production_env,
)


def _valid_env_values() -> dict[str, str]:
    return {
        name: f"distinct-production-value-{index}-long-enough"
        for index, name in enumerate(REQUIRED_PRODUCTION_SECRETS)
    }


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
    del values["CONTROL_PLANE_MASTER_KEY"]
    env_file = tmp_path / ".env.production"
    _write_env_file(env_file, values)

    errors = validate_production_env(env_file)

    assert any("REDIS_PASSWORD" in error for error in errors)
    assert any("SECRET_KEY" in error for error in errors)
    assert any("MASTER_ONBOARDING_KEY" in error for error in errors)
    assert any("CONTROL_PLANE_MASTER_KEY" in error for error in errors)
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


def test_production_env_cli_fails_for_missing_file(tmp_path, capsys):
    exit_code = main(["--env-file", str(tmp_path / "missing.env")])

    assert exit_code == 1
    assert "does not exist" in capsys.readouterr().out
