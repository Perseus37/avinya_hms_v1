import argparse
import ipaddress
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
    "SEED_ADMIN_PASSWORD",
    "ENVELOPE_ENCRYPTION_SALT",
    "BACKUP_ENCRYPTION_SALT",
    "METRICS_SCRAPE_TOKEN",
)
OPTIONAL_PRODUCTION_SECRETS = (
    "CONTROL_PLANE_MASTER_KEY",
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

    legacy_auth_setting = values.get("CONTROL_PLANE_LEGACY_MASTER_KEY_ENABLED")
    if legacy_auth_setting is None or legacy_auth_setting.strip().lower() not in {
        "true",
        "false",
    }:
        errors.append("CONTROL_PLANE_LEGACY_MASTER_KEY_ENABLED must be explicitly true or false.")
    elif legacy_auth_setting.strip().lower() != "false":
        errors.append("CONTROL_PLANE_LEGACY_MASTER_KEY_ENABLED must be false in production.")

    if legacy_auth_setting and legacy_auth_setting.strip().lower() == "true":
        legacy_key = values.get("CONTROL_PLANE_MASTER_KEY")
        if not legacy_key or not legacy_key.strip():
            errors.append(
                "CONTROL_PLANE_MASTER_KEY is required when legacy Control Plane authentication is enabled."
            )

    trusted_proxies = values.get("TRUSTED_PROXY_IPS", "")
    if not trusted_proxies.strip():
        errors.append("TRUSTED_PROXY_IPS must list the trusted reverse proxy IPs or CIDRs.")
    else:
        for proxy in trusted_proxies.split(","):
            try:
                network = ipaddress.ip_network(proxy.strip(), strict=False)
            except ValueError:
                errors.append("TRUSTED_PROXY_IPS contains an invalid IP address or CIDR.")
                break
            if network.prefixlen == 0:
                errors.append("TRUSTED_PROXY_IPS must not trust all IP addresses.")
                break

    for name in OPTIONAL_PRODUCTION_SECRETS:
        value = values.get(name)
        if value and (
            "REPLACE_" in value.upper()
            or PLACEHOLDER_VALUE.fullmatch(value.strip())
        ):
            errors.append(f"{name} still contains a placeholder or example value.")

    # Wider production configuration & safety validation
    for name, value in values.items():
        if value and isinstance(value, str):
            if "REPLACE_" in value.upper():
                msg = f"{name} still contains a placeholder or example value."
                if msg not in errors:
                    errors.append(msg)
            elif (
                name not in ("ENVIRONMENT", "APP_TIMEZONE", "POSTGRES_DB", "DEFAULT_TENANT_SLUG")
                and PLACEHOLDER_VALUE.fullmatch(value.strip())
            ):
                msg = f"{name} still contains a placeholder or example value."
                if msg not in errors:
                    errors.append(msg)

    env_mode = values.get("ENVIRONMENT")
    if env_mode and env_mode.strip().lower() not in {"production", "staging"}:
        errors.append(f"ENVIRONMENT must be 'production' or 'staging', got: '{env_mode}'.")

    auto_schema = values.get("DEVELOPMENT_AUTO_CREATE_SCHEMA")
    if auto_schema and auto_schema.strip().lower() == "true":
        errors.append("DEVELOPMENT_AUTO_CREATE_SCHEMA must be false in production.")

    retention_exec = values.get("PRIVACY_ALLOW_RETENTION_EXECUTE")
    if retention_exec and retention_exec.strip().lower() == "true":
        errors.append("PRIVACY_ALLOW_RETENTION_EXECUTE must be false during deployment.")

    base_domain = values.get("BASE_DOMAIN")
    if base_domain and (base_domain.strip().lower() == "localhost" or "." not in base_domain):
        errors.append("BASE_DOMAIN must be a fully qualified domain name (FQDN), not localhost.")

    cors_origins = values.get("CORS_ORIGINS")
    if cors_origins:
        if "*" in cors_origins:
            errors.append("CORS_ORIGINS must not use wildcard '*' in production.")
        elif any(origin.strip().startswith("http://") for origin in cors_origins.split(",") if "localhost" not in origin):
            errors.append("CORS_ORIGINS must enforce HTTPS in production.")

    admin_email = values.get("SEED_ADMIN_EMAIL")
    if admin_email and "@" not in admin_email:
        errors.append("SEED_ADMIN_EMAIL must be a valid email address.")

    # Encryption salt hardening: reject known default salt and weak salts
    known_default_salts = {"avinya_secure_salt_2026", "avinya_backup_salt_2026"}
    for salt_name in ("ENVELOPE_ENCRYPTION_SALT", "BACKUP_ENCRYPTION_SALT"):
        salt_val = values.get(salt_name, "")
        if salt_val and salt_val.strip() in known_default_salts:
            errors.append(f"{salt_name} must not use known default salt '{salt_val.strip()}'.")
        elif salt_val and len(salt_val.strip()) < 16:
            errors.append(f"{salt_name} must contain at least 16 characters.")

    # Token lifetime limits: enforce session safety ceilings in production
    access_token_expire = values.get("ACCESS_TOKEN_EXPIRE_MINUTES")
    if access_token_expire:
        try:
            exp_mins = int(access_token_expire.strip())
            if exp_mins <= 0 or exp_mins > 60:
                errors.append("ACCESS_TOKEN_EXPIRE_MINUTES must be between 1 and 60 minutes in production.")
        except ValueError:
            errors.append("ACCESS_TOKEN_EXPIRE_MINUTES must be a valid integer.")

    cp_token_expire = values.get("CONTROL_PLANE_ACCESS_TOKEN_MINUTES")
    if cp_token_expire:
        try:
            cp_mins = int(cp_token_expire.strip())
            if cp_mins <= 0 or cp_mins > 60:
                errors.append("CONTROL_PLANE_ACCESS_TOKEN_MINUTES must be between 1 and 60 minutes in production.")
        except ValueError:
            errors.append("CONTROL_PLANE_ACCESS_TOKEN_MINUTES must be a valid integer.")

    # Database TLS settings: reject disabled TLS in production
    pg_sslmode = values.get("POSTGRES_SSLMODE")
    if pg_sslmode:
        if pg_sslmode.strip().lower() in {"disable", "allow"}:
            errors.append("POSTGRES_SSLMODE must not disable TLS in production (use prefer, require, verify-ca, or verify-full).")

    # Metrics scrape token entropy check
    metrics_token = values.get("METRICS_SCRAPE_TOKEN", "")
    if metrics_token and len(metrics_token.strip()) < 16:
        errors.append("METRICS_SCRAPE_TOKEN must contain at least 16 characters.")

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
