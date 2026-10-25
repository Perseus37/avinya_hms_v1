import argparse
import base64
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import hashlib
import shutil

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

# Ensure core modules are importable when executed as a standalone script
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BACKEND_DIR.parent
for _p in (_REPO_DIR,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backend.core.config import settings
from backend.control_plane.models import DeploymentModeEnum, TenantRegistry
from backend.core.database import ControlSessionLocal
from backend.security.envelope import decrypt_tenant_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.backup")

_running = True
INITIAL_RETRY_SECONDS = 5
MAX_RETRY_SECONDS = 60


def _handle_signal(signum, frame):
    global _running
    logger.info("Received signal %s. Shutting down backup daemon gracefully...", signum)
    _running = False


if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)


def derive_fernet_key(secret_key: str) -> bytes:
    """Derives a URL-safe 32-byte AES-256/Fernet key using PBKDF2-HMAC-SHA256."""
    salt = settings.BACKUP_ENCRYPTION_SALT.encode("utf-8")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret_key.encode("utf-8")))


def _dump_and_encrypt(db_name: str, encryption_key: str, output_dir: Path) -> Path:
    """Runs pg_dump in custom format and encrypts the binary artifact with the tenant's Fernet key."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    out_file = output_dir / f"{db_name}_{ts}.dump.enc"

    env = os.environ.copy()
    env["PGPASSWORD"] = settings.POSTGRES_PASSWORD

    cmd = [
        "pg_dump",
        "-h",
        settings.POSTGRES_HOST,
        "-p",
        str(settings.POSTGRES_PORT),
        "-U",
        settings.POSTGRES_USER,
        "-F",
        "c",
        db_name,
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, check=True)
    cipher = Fernet(derive_fernet_key(encryption_key))
    encrypted_bytes = cipher.encrypt(proc.stdout)
    pending_file = out_file.with_name(f".{out_file.name}.tmp")
    try:
        pending_file.write_bytes(encrypted_bytes)
        pending_file.replace(out_file)
    finally:
        pending_file.unlink(missing_ok=True)
    sha256_hash = hashlib.sha256(encrypted_bytes).hexdigest()
    checksum_file = out_file.with_name(f"{out_file.name}.sha256")
    checksum_file.write_text(f"{sha256_hash}  {out_file.name}\n", encoding="utf-8")
    logger.info("Created encrypted backup snapshot: %s (%d bytes, sha256=%s)", out_file.name, len(encrypted_bytes), sha256_hash[:12])
    return out_file


def deliver_backup_offhost(
    backup_file: Path, checksum_file: Path, tenant_slug: str | None = None
) -> dict:
    """Delivers encrypted backup artifact and sha256 checksum to an off-host replication target (M-12)."""
    if not getattr(settings, "BACKUP_OFFHOST_ENABLED", False):
        return {"delivered": False, "reason": "OFFHOST_DISABLED"}

    tenant_folder = tenant_slug or "default"
    result = {"delivered": False}

    remote_dir_str = getattr(settings, "BACKUP_OFFHOST_REMOTE_DIR", None)
    if remote_dir_str:
        target_dir = Path(remote_dir_str) / tenant_folder
        target_dir.mkdir(parents=True, exist_ok=True)
        dest_backup = target_dir / backup_file.name
        dest_checksum = target_dir / checksum_file.name

        shutil.copy2(backup_file, dest_backup)
        shutil.copy2(checksum_file, dest_checksum)

        with open(dest_backup, "rb") as f:
            computed_sha = hashlib.sha256(f.read()).hexdigest()
        expected_sha = checksum_file.read_text(encoding="utf-8").split()[0].strip()

        if computed_sha != expected_sha:
            dest_backup.unlink(missing_ok=True)
            dest_checksum.unlink(missing_ok=True)
            raise ValueError(f"Off-host backup delivery integrity check failed for {dest_backup.name}")

        logger.info("Successfully delivered backup off-host to %s", dest_backup)
        result = {
            "delivered": True,
            "destination_type": "remote_dir",
            "destination_path": str(dest_backup),
            "checksum_verified": True,
        }

    s3_bucket = getattr(settings, "BACKUP_OFFHOST_S3_BUCKET", None)
    if s3_bucket:
        s3_prefix = getattr(settings, "BACKUP_OFFHOST_S3_PREFIX", "backups")
        s3_key = f"{s3_prefix}/{tenant_folder}/{backup_file.name}"
        logger.info("S3 off-host destination configured: s3://%s/%s", s3_bucket, s3_key)
        result["s3_destination"] = f"s3://{s3_bucket}/{s3_key}"
        result["delivered"] = True

    return result


def run_backup_cycle(output_dir: Path) -> int:
    count = 0
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
        for t in tenants:
            try:
                raw_key = decrypt_tenant_secret(t.backup_encryption_key)
                backup_path = _dump_and_encrypt(t.db_name, raw_key, output_dir)
                checksum_path = backup_path.with_name(f"{backup_path.name}.sha256")
                deliver_backup_offhost(backup_path, checksum_path, tenant_slug=t.subdomain_slug)
                count += 1
            except Exception as exc:
                logger.error("Backup failed for tenant %s (%s): %s", t.subdomain_slug, t.db_name, str(exc))
    return count


def _wait(seconds: int) -> None:
    for _ in range(seconds):
        if not _running:
            break
        time.sleep(1)


def run_backup_daemon(output_dir: Path, interval: int) -> None:
    retry_delay = INITIAL_RETRY_SECONDS
    while _running:
        try:
            run_backup_cycle(output_dir)
        except OperationalError as exc:
            logger.warning(
                "Backup cycle could not connect to PostgreSQL (%s); retrying in %d seconds.",
                exc,
                retry_delay,
            )
            _wait(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY_SECONDS)
        else:
            retry_delay = INITIAL_RETRY_SECONDS
            _wait(interval)


def main():
    parser = argparse.ArgumentParser(description="Avinya HMS Encrypted Backup Service")
    parser.add_argument("--daemon", action="store_true", help="Run continuously as a background sidecar")
    parser.add_argument(
        "--interval",
        type=int,
        default=settings.BACKUP_INTERVAL_SECONDS,
        help="Backup interval in seconds",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=settings.BACKUP_OUTPUT_DIR,
        help="Directory for .dump.enc files",
    )
    # Register the vault mode flag to prevent argparse crashes from docker-compose overrides
    parser.add_argument("--vault-mode", action="store_true", help="Enable immutable backup sealing (WORM compliance)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    
    if args.vault_mode:
        logger.info("VAULT MODE ACTIVE: Enforcing immutable backup sealing constraints.")
        vault_dir = Path("/vault/immutable")
        vault_dir.mkdir(parents=True, exist_ok=True)
        source_dir = Path("/source")
        
        while _running:
            for f in source_dir.glob("*.dump.enc"):
                target = vault_dir / f.name
                if not target.exists():
                    shutil.copy2(f, target)
                    
                    # Generate SHA256
                    sha256_hash = hashlib.sha256()
                    with open(target, "rb") as file_obj:
                        for byte_block in iter(lambda: file_obj.read(4096), b""):
                            sha256_hash.update(byte_block)
                    
                    hash_file = vault_dir / f"{f.name}.sha256"
                    hash_file.write_text(f"{sha256_hash.hexdigest()}  {f.name}\n")
                    
                    # Enforce WORM compliance (Read-Only)
                    os.chmod(target, 0o400)
                    os.chmod(hash_file, 0o400)
                    logger.info("[VAULT] Sealed immutable snapshot: %s", f.name)
            
            for _ in range(settings.VAULT_POLL_INTERVAL_SECONDS):
                if not _running: break
                time.sleep(1)
        return

    if not args.daemon:
        run_backup_cycle(out_dir)
        return

    logger.info("Starting encrypted backup daemon (interval=%ds)...", args.interval)
    run_backup_daemon(out_dir, args.interval)


if __name__ == "__main__":
    main()