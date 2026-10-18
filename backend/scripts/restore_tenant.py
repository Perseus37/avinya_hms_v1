import argparse
import logging
import os
from pathlib import Path
import subprocess
import sys
from cryptography.fernet import Fernet
from sqlalchemy.engine import make_url

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BACKEND_DIR.parent
for _path in (_REPO_DIR,):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from backend.security.envelope import decrypt_tenant_secret
from backend.scripts.backup_service import derive_fernet_key
from backend.core.config import settings
from backend.control_plane.models import DeploymentModeEnum, TenantRegistry
from backend.core.database import ControlSessionLocal, ensure_postgres_db_exists

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.restore")


def restore_encrypted_backup(
    encrypted_file_path: str,
    target_db_name: str,
    backup_encryption_key: str,
    promote_to_cloud: bool = False,
) -> None:
    enc_path = Path(encrypted_file_path)
    if not enc_path.exists():
        raise FileNotFoundError(f"Encrypted backup file not found: {encrypted_file_path}")

    raw_key = decrypt_tenant_secret(backup_encryption_key)
    cipher = Fernet(derive_fernet_key(raw_key))
    decrypted_bytes = cipher.decrypt(enc_path.read_bytes())

    temp_dump = enc_path.with_suffix(".restoring.dump")
    temp_dump.write_bytes(decrypted_bytes)

    try:
        ensure_postgres_db_exists(target_db_name)
        url_obj = make_url(settings.DATABASE_URL)
        env = {**os.environ, "PGPASSWORD": url_obj.password or ""}
        cmd = [
            "pg_restore",
            "-h", url_obj.host or "db",
            "-p", str(url_obj.port or 5432),
            "-U", url_obj.username or "user",
            "-d", target_db_name,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl",
            str(temp_dump),
        ]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("pg_restore failed with exit code %s: %s", res.returncode, (res.stderr or "").strip())
            raise RuntimeError(f"pg_restore failed with exit code {res.returncode}.")

        logger.info("Successfully restored encrypted backup into database '%s'.", target_db_name)

        if promote_to_cloud:
            with ControlSessionLocal() as control_db:
                tenant = control_db.query(TenantRegistry).filter(TenantRegistry.db_name == target_db_name).first()
                if tenant:
                    tenant.deployment_mode = DeploymentModeEnum.CLOUD
                    control_db.commit()
                    logger.info(
                        "INSTANT CLOUD FAILOVER ACTIVE: '%s.%s' is now serving live from Cloud!",
                        tenant.subdomain_slug,
                        settings.BASE_DOMAIN,
                    )
    finally:
        temp_dump.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Decrypt and restore an AES-256 hospital database backup.")
    parser.add_argument("--file", required=True, help="Path to the .dump.enc file")
    parser.add_argument("--db-name", required=True, help="Target database name, e.g., hms_sunrise")
    parser.add_argument("--key", required=True, help="Tenant's backup_encryption_key")
    parser.add_argument("--promote-to-cloud", action="store_true", help="Failover an On-Premise hospital directly to Cloud")
    args = parser.parse_args()

    restore_encrypted_backup(args.file, args.db_name, args.key, args.promote_to_cloud)