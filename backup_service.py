import base64
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
from pathlib import Path
import subprocess

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.engine import make_url

from config import settings
from control_models import DeploymentModeEnum, TenantRegistry
from database import ControlSessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.backup")

RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "7"))


def derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        settings.BACKUP_ENCRYPTION_SALT.encode("utf-8"),
        100_000,
    )
    return base64.urlsafe_b64encode(digest)


def prune_old_backups(backup_dir: Path, retention_days: int = RETENTION_DAYS) -> int:
    """Deletes encrypted backup snapshots older than retention_days."""
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=retention_days)).timestamp()
    removed = 0
    for enc_file in backup_dir.glob("*.dump.enc"):
        if enc_file.stat().st_mtime < cutoff_ts:
            enc_file.unlink(missing_ok=True)
            removed += 1
            logger.info("Pruned expired backup snapshot: %s", enc_file)
    return removed


def backup_all_tenants() -> None:
    """Dumps and AES-256 encrypts every active hospital database for catastrophic recovery."""
    backup_dir = Path("backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    url_obj = make_url(settings.DATABASE_URL)

    with ControlSessionLocal() as control_db:
        tenants = (
            control_db.execute(
                select(TenantRegistry).where(
                    TenantRegistry.is_active == True,
                    TenantRegistry.deployment_mode == DeploymentModeEnum.CLOUD,
                )
            )
            .scalars()
            .all()
        )

        for tenant in tenants:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            raw_dump = backup_dir / f"{tenant.db_name}_{timestamp}.dump"
            encrypted_file = backup_dir / f"{tenant.db_name}_{timestamp}.dump.enc"

            env = {**os.environ, "PGPASSWORD": url_obj.password or ""}
            cmd = [
                "pg_dump",
                "-h", url_obj.host or "db",
                "-p", str(url_obj.port or 5432),
                "-U", url_obj.username or "user",
                "-Fc",
                "-f", str(raw_dump),
                tenant.db_name,
            ]
            subprocess.run(cmd, env=env, check=True)

            cipher = Fernet(derive_fernet_key(tenant.backup_encryption_key))
            encrypted_bytes = cipher.encrypt(raw_dump.read_bytes())
            encrypted_file.write_bytes(encrypted_bytes)
            raw_dump.unlink(missing_ok=True)
            logger.info("Encrypted catastrophic backup created: %s", encrypted_file)

    prune_old_backups(backup_dir)

if __name__ == "__main__":
    backup_all_tenants()