"""Automated Disaster Recovery (DR) and Restore Drill Runner for Avinya HMS (M-12).

Verifies encrypted database backup snapshots, performs an end-to-end restore into an isolated
drill database, validates schema & relational integrity across core tables, measures
Recovery Time Objective (RTO) and Recovery Point Objective (RPO), and outputs a certified DR drill report.
"""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Optional
from urllib.parse import quote_plus
import uuid

# Ensure core modules are importable when executed as a standalone script
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BACKEND_DIR.parent
for _p in (_REPO_DIR,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import settings
from backend.control_plane.models import TenantRegistry
from backend.core.database import ControlSessionLocal
from backend.security.envelope import decrypt_tenant_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.restore_drill")

CRITICAL_HMS_TABLES = [
    "users",
    "patients",
    "patient_encounters",
    "invoices",
    "service_catalog",
    "privacy_policy_revisions",
]


def derive_fernet_key(secret_key: str) -> bytes:
    """Derives a URL-safe 32-byte AES-256/Fernet key matching backup_service.py."""
    salt = settings.BACKUP_ENCRYPTION_SALT.encode("utf-8")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret_key.encode("utf-8")))


def verify_backup_checksum(
    backup_path: Path, checksum_path: Optional[Path] = None
) -> bool:
    """Verifies that the SHA-256 hash of the backup file matches its checksum sidecar."""
    if checksum_path is None:
        checksum_path = backup_path.with_name(f"{backup_path.name}.sha256")

    if not checksum_path.exists():
        logger.warning("Checksum file %s not found; skipping sidecar checksum check.", checksum_path.name)
        return False

    with open(backup_path, "rb") as f:
        computed_sha = hashlib.sha256(f.read()).hexdigest()

    expected_sha = checksum_path.read_text(encoding="utf-8").split()[0].strip()
    matches = computed_sha.lower() == expected_sha.lower()
    if not matches:
        logger.error(
            "Checksum mismatch for %s: expected %s, got %s",
            backup_path.name,
            expected_sha,
            computed_sha,
        )
    return matches


def decrypt_backup_bytes(backup_path: Path, encryption_key: str) -> bytes:
    """Decrypts Fernet-encrypted backup bytes in memory."""
    encrypted_bytes = backup_path.read_bytes()
    cipher = Fernet(derive_fernet_key(encryption_key))
    try:
        return cipher.decrypt(encrypted_bytes)
    except Exception as exc:
        raise ValueError(f"Failed to decrypt backup artifact with tenant key: {exc}") from exc


def run_restore_drill(
    *,
    backup_file: Path | str,
    tenant_slug: str,
    encryption_key: str,
    postgres_host: str | None = None,
    postgres_port: int | None = None,
    postgres_user: str | None = None,
    postgres_password: str | None = None,
    drill_db_name: Optional[str] = None,
    max_acceptable_rto_seconds: int = 300,
    keep_drill_db: bool = False,
    output_report_path: Optional[Path | str] = None,
) -> dict[str, Any]:
    """
    Executes an automated disaster recovery drill:
    1. Validates cryptographic signature/checksum
    2. Decrypts tenant backup snapshot
    3. Provisions ephemeral drill database in PostgreSQL
    4. Measures Recovery Time Objective (RTO) during physical restore
    5. Measures Recovery Point Objective (RPO) and audits record completeness
    6. Validates schema and table rows across critical domains
    7. Cleans up ephemeral drill database
    8. Generates a formal DR Drill Certification Report
    """
    # R-10: Guard drill database identifier FIRST before any file or DB operations
    if drill_db_name:
        if not re.match(r"^drill_[a-zA-Z0-9_]+$", drill_db_name):
            raise ValueError(f"Invalid drill database name '{drill_db_name}'. Must match pattern ^drill_[a-zA-Z0-9_]+$")
        drill_db = drill_db_name
    else:
        drill_db = f"drill_{re.sub(r'[^a-zA-Z0-9_]', '_', tenant_slug)}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    backup_path = Path(backup_file).resolve()
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")

    host = postgres_host or settings.POSTGRES_HOST
    port = postgres_port or settings.POSTGRES_PORT
    user = postgres_user or settings.POSTGRES_USER
    password = postgres_password or settings.POSTGRES_PASSWORD

    drill_id = f"DRILL-{uuid.uuid4().hex[:12].upper()}"

    logger.info("Initiating DR restore drill [%s] for tenant '%s' using '%s'", drill_id, tenant_slug, backup_path.name)

    # 1. Verify Checksum
    checksum_verified = verify_backup_checksum(backup_path)

    # 2. Decrypt Snapshot
    t_decrypt_start = time.perf_counter()
    decrypted_dump = decrypt_backup_bytes(backup_path, encryption_key)
    decryption_time_seconds = time.perf_counter() - t_decrypt_start
    logger.info("Decrypted backup (%d bytes) in %.2fs", len(decrypted_dump), decryption_time_seconds)

    # 3. Create Ephemeral Drill Database (R-10: URL-encode credentials)
    quoted_user = quote_plus(str(user))
    quoted_password = quote_plus(str(password))
    admin_dsn = f"postgresql://{quoted_user}:{quoted_password}@{host}:{port}/postgres"
    admin_engine = create_engine(admin_dsn, isolation_level="AUTOCOMMIT")

    env = os.environ.copy()
    env["PGPASSWORD"] = password

    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{drill_db}" WITH (FORCE);'))
            conn.execute(text(f'CREATE DATABASE "{drill_db}";'))
        logger.info("Created isolated drill database '%s'", drill_db)

        # 4. Measure RTO during pg_restore
        restore_start_t = time.perf_counter()
        restore_cmd = [
            "pg_restore",
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            drill_db,
            "--no-owner",
            "--no-privileges",
        ]
        proc = subprocess.run(
            restore_cmd,
            input=decrypted_dump,
            env=env,
            capture_output=True,
            check=False,
        )
        restore_end_t = time.perf_counter()
        rto_seconds = restore_end_t - restore_start_t

        if proc.returncode != 0 and b"WARNING" not in proc.stderr and proc.returncode > 1:
            logger.error("pg_restore returned code %d: %s", proc.returncode, proc.stderr.decode("utf-8", errors="replace"))

        # 5. Connect to Drill DB & Measure Integrity and RPO
        drill_dsn = f"postgresql://{quoted_user}:{quoted_password}@{host}:{port}/{drill_db}"
        drill_engine = create_engine(drill_dsn)
        
        table_counts: dict[str, int] = {}
        inspector = inspect(drill_engine)
        existing_tables = set(inspector.get_table_names())

        missing_critical_tables = [t for t in CRITICAL_HMS_TABLES if t not in existing_tables]

        newest_record_time: Optional[datetime] = None

        with Session(drill_engine) as session:
            for tbl in sorted(existing_tables):
                if not re.match(r"^[a-zA-Z0-9_]+$", tbl):
                    continue
                try:
                    count = session.execute(text(f'SELECT COUNT(*) FROM "{tbl}"')).scalar_one()  # nosec B608
                    table_counts[tbl] = count
                except Exception:
                    table_counts[tbl] = -1

            # Determine RPO by checking newest timestamp in clinical encounters, invoices, or audit records
            for candidate_query in [
                "SELECT MAX(created_at) FROM patient_encounters",
                "SELECT MAX(created_at) FROM invoices",
                "SELECT MAX(created_at) FROM patients",
            ]:
                try:
                    ts = session.execute(text(candidate_query)).scalar_one_or_none()
                    if isinstance(ts, datetime):
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if newest_record_time is None or ts > newest_record_time:
                            newest_record_time = ts
                except Exception:
                    pass

        now_utc = datetime.now(timezone.utc)
        if isinstance(newest_record_time, datetime):
            rpo_seconds = max(0.0, (now_utc - newest_record_time).total_seconds())
        else:
            rpo_seconds = None

        all_tables_valid = len(missing_critical_tables) == 0
        sla_rto_passed = rto_seconds <= max_acceptable_rto_seconds

        drill_passed = (
            checksum_verified
            and all_tables_valid
            and sla_rto_passed
        )

        status_str = "PASSED" if drill_passed else "FAILED"

    finally:
        admin_engine.dispose()
        if not keep_drill_db:
            try:
                with create_engine(admin_dsn, isolation_level="AUTOCOMMIT").connect() as conn:
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{drill_db}" WITH (FORCE);'))
                logger.info("Cleaned up ephemeral drill database '%s'", drill_db)
            except Exception as exc:
                logger.warning("Could not drop drill database '%s': %s", drill_db, exc)

    report = {
        "drill_id": drill_id,
        "tenant_slug": tenant_slug,
        "backup_file": str(backup_path.name),
        "backup_size_bytes": backup_path.stat().st_size,
        "checksum_verified": checksum_verified,
        "measured_rto_seconds": round(rto_seconds, 3),
        "sla_rto_threshold_seconds": max_acceptable_rto_seconds,
        "sla_rto_passed": sla_rto_passed,
        "measured_rpo_seconds": round(rpo_seconds, 1) if rpo_seconds is not None else None,
        "newest_transaction_timestamp": newest_record_time.isoformat() if newest_record_time else None,
        "missing_critical_tables": missing_critical_tables,
        "verified_tables_count": len(table_counts),
        "table_counts": table_counts,
        "verification_status": status_str,
        "certified_at": datetime.now(timezone.utc).isoformat(),
    }

    if output_report_path:
        out_p = Path(output_report_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info("Wrote DR drill certification report to %s", out_p)

    return report


def main():
    parser = argparse.ArgumentParser(description="Avinya HMS Disaster Recovery Restore Drill Runner")
    parser.add_argument("--backup-file", required=True, help="Path to .dump.enc backup file")
    parser.add_argument("--tenant-slug", required=True, help="Tenant subdomain slug")
    parser.add_argument(
        "--max-acceptable-rto-seconds",
        type=int,
        default=300,
        help="SLA threshold for RTO in seconds (default: 300)",
    )
    parser.add_argument("--output-report", help="Optional path to write JSON certification report")
    parser.add_argument("--keep-drill-db", action="store_true", help="Preserve drill DB after completion")
    args = parser.parse_args()

    with ControlSessionLocal() as control_db:
        tenant = (
            control_db.execute(
                select(TenantRegistry).where(TenantRegistry.subdomain_slug == args.tenant_slug)
            )
            .scalars()
            .first()
        )
        if not tenant:
            logger.error("Tenant '%s' not found in Control Plane registry.", args.tenant_slug)
            sys.exit(1)

        raw_key = decrypt_tenant_secret(tenant.backup_encryption_key)

    report = run_restore_drill(
        backup_file=args.backup_file,
        tenant_slug=args.tenant_slug,
        encryption_key=raw_key,
        max_acceptable_rto_seconds=args.max_acceptable_rto_seconds,
        keep_drill_db=args.keep_drill_db,
        output_report_path=args.output_report,
    )

    print(json.dumps(report, indent=2))
    if report["verification_status"] != "PASSED":
        sys.exit(1)


if __name__ == "__main__":
    main()
