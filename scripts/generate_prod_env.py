"""Generates a cryptographically secure .env.production template for Avinya HMS."""
import secrets
from pathlib import Path


def generate_production_env() -> None:
    jwt_secret = secrets.token_hex(64)
    backup_salt = secrets.token_hex(32)
    control_plane_key = f"cp_live_{secrets.token_urlsafe(32)}"
    db_password = secrets.token_urlsafe(24)

    env_content = f"""# =====================================================================
# AVINYA HMS v3.0 — PRODUCTION ENVIRONMENT CONFIGURATION
# Generated via scripts/generate_prod_env.py
# =====================================================================
ENVIRONMENT=production
DEPLOYMENT_MODE=CLOUD
BASE_DOMAIN=avinyahms.com

# --- Cryptographic Secrets (Keep Offline Backup of BACKUP_ENCRYPTION_SALT!) ---
SECRET_KEY={jwt_secret}
BACKUP_ENCRYPTION_SALT={backup_salt}
CONTROL_PLANE_MASTER_KEY={control_plane_key}

# --- PostgreSQL Production Credentials ---
POSTGRES_USER=avinya_prod_admin
POSTGRES_PASSWORD={db_password}
POSTGRES_DB=hms_default
DATABASE_URL=postgresql+psycopg2://avinya_prod_admin:{db_password}@db:5432/hms_default
CONTROL_DATABASE_URL=postgresql+psycopg2://avinya_prod_admin:{db_password}@db:5432/hms_control_plane

# --- Redis & Backup Retention ---
REDIS_URL=redis://redis:6379/0
BACKUP_RETENTION_DAYS=30
ACCESS_TOKEN_EXPIRE_MINUTES=480
"""
    out_path = Path(".env.production.example")
    out_path.write_text(env_content, encoding="utf-8")
    print(f"Generated production config at: {out_path.resolve()}")


if __name__ == "__main__":
    generate_production_env()