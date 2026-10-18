import argparse
import os
from pathlib import Path
import re
import stat
import sys

from dotenv import dotenv_values


REQUIRED_PRODUCTION_SECRETS = (
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
    "SECRET_KEY",
    "MASTER_ONBOARDING_KEY",
    "CONTROL_PLANE_MASTER_KEY",
    "SEED_ADMIN_PASSWORD",
    "ENVELOPE_ENCRYPTION_SALT",
    "BACKUP_ENCRYPTION_SALT",
)
OPTIONAL_PRODUCTION_SECRETS = (
    "SMTP_PASSWORD",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "CF_DNS_API_TOKEN",
)
PLACEHOLDER_VALUE = re.compile(
    r"^(?:test|example|changeme|change_me|password|secret)(?:[-_].*)?$",
    re.IGNORECASE,
)


def validate_production_env(env_file: Path) -> list[str]:
    if not env_file.is_file():
        return [f"Environment file does not exist: {env_file}"]

    values = dotenv_values(env_file, interpolate=False)
    errors = []

    for name in REQUIRED_PRODUCTION_SECRETS:
        value = values.get(name)
        if not value or not value.strip():
            errors.append(f"{name} is missing or empty.")
            continue
        if "REPLACE_" in value.upper() or PLACEHOLDER_VALUE.fullmatch(value.strip()):
            errors.append(f"{name} still contains a placeholder or example value.")

    for name in OPTIONAL_PRODUCTION_SECRETS:
        value = values.get(name)
        if value and (
            "REPLACE_" in value.upper()
            or PLACEHOLDER_VALUE.fullmatch(value.strip())
        ):
            errors.append(f"{name} still contains a placeholder or example value.")

    populated_secrets = {}
    for name in REQUIRED_PRODUCTION_SECRETS + OPTIONAL_PRODUCTION_SECRETS:
        value = values.get(name)
        if value and value.strip():
            populated_secrets.setdefault(value, []).append(name)
    for names in populated_secrets.values():
        if len(names) > 1:
            errors.append(
                "Secret values must be unique; repeated across: "
                + ", ".join(names)
                + "."
            )

    secret_key = values.get("SECRET_KEY", "")
    if secret_key and len(secret_key) < 32:
        errors.append("SECRET_KEY must contain at least 32 characters.")

    if os.name != "nt":
        mode = stat.S_IMODE(env_file.stat().st_mode)
        if mode & 0o077:
            errors.append(
                "Environment file permissions are too broad; restrict access to "
                "the deployment account (for example, chmod 600)."
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check production environment secrets without printing values."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env.production"),
        help="Production environment file (default: .env.production).",
    )
    args = parser.parse_args(argv)

    errors = validate_production_env(args.env_file)
    if errors:
        print("Production environment validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Production environment secrets and file permissions are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
