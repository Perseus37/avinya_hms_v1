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
from control_models import TenantRegistry
from database import ControlSessionLocal
from security.envelope import decrypt_tenant_secret

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


def _dump_and_encrypt(db_name: str, encryption_secret: str, backup_dir: Path) -> None:
    url_obj = make_url(settings.DATABASE_URL)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    raw_dump = backup_dir / f"{db_name}_{timestamp}.dump"
    encrypted_file = backup_dir / f"{db_name}_{timestamp}.dump.enc"

    env = {**os.environ, "PGPASSWORD": url_obj.password or ""}
    cmd = [
        "pg_dump",
        "-h", url_obj.host or "db",
        "-p", str(url_obj.port or 5432),
        "-U", url_obj.username or "user",
        "-Fc",
        "-f", str(raw_dump),
        db_name,
    ]
    subprocess.run(cmd, env=env, check=True)

    cipher = Fernet(derive_fernet_key(encryption_secret))
    encrypted_file.write_bytes(cipher.encrypt(raw_dump.read_bytes()))
    raw_dump.unlink(missing_ok=True)
    logger.info("Encrypted backup created: %s", encrypted_file.name)


def prune_old_backups(backup_dir: Path, retention_days: int = RETENTION_DAYS) -> int:
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=retention_days)).timestamp()
    removed = 0
    for enc_file in backup_dir.glob("*.dump.enc"):
        if enc_file.stat().st_mtime < cutoff_ts:
            enc_file.unlink(missing_ok=True)
            removed += 1
    return removed


def backup_all_tenants() -> None:
    """
    1. Backs up hms_control_plane (which stores all tenant keys, registry, and MPI pointers).
    2. Backs up every active hospital tenant database.
    """
    backup_dir = Path("backups")
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 1. Back up the Central Control Plane DB (encrypted with master SECRET_KEY)
    _dump_and_encrypt(settings.CONTROL_PLANE_DB_NAME, settings.SECRET_KEY, backup_dir)

    # 2. Back up each Hospital Tenant DB
    with ControlSessionLocal() as control_db:
        tenants = (
            control_db.execute(select(TenantRegistry).where(TenantRegistry.is_active == True))
            .scalars()
            .all()
        )
        for tenant in tenants:
            raw_key = decrypt_tenant_secret(tenant.backup_encryption_key)
            _dump_and_encrypt(tenant.db_name, raw_key, backup_dir)

    prune_old_backups(backup_dir)

if __name__ == "__main__":
    import sys
    import time
    if "--daemon" in sys.argv:
        while True:
            backup_all_tenants()
            time.sleep(int(os.getenv("BACKUP_INTERVAL_SECONDS", "21600")))
    else:
        backup_all_tenants()