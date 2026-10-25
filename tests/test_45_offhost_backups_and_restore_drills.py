"""Tests for M-12 Off-host backup delivery and automated disaster recovery restore drills."""

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from backend.core.config import settings
from backend.scripts.backup_service import (
    deliver_backup_offhost,
    derive_fernet_key,
)
from backend.scripts.restore_drill import (
    decrypt_backup_bytes,
    run_restore_drill,
    verify_backup_checksum,
)


@pytest.fixture
def temp_backup_env(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    offhost_dir = tmp_path / "offhost_target"
    offhost_dir.mkdir(parents=True, exist_ok=True)

    secret_key = "test_tenant_secret_key_2026"
    fernet_key = derive_fernet_key(secret_key)
    cipher = Fernet(fernet_key)

    raw_data = b"PG_DUMP_SYNTHETIC_BINARY_DATA_TEST"
    encrypted_data = cipher.encrypt(raw_data)

    backup_file = backup_dir / "hms_tenant1_20261025.dump.enc"
    backup_file.write_bytes(encrypted_data)

    sha256_hash = hashlib.sha256(encrypted_data).hexdigest()
    checksum_file = backup_dir / f"{backup_file.name}.sha256"
    checksum_file.write_text(f"{sha256_hash}  {backup_file.name}\n", encoding="utf-8")

    return {
        "backup_dir": backup_dir,
        "offhost_dir": offhost_dir,
        "backup_file": backup_file,
        "checksum_file": checksum_file,
        "secret_key": secret_key,
        "raw_data": raw_data,
        "sha256": sha256_hash,
    }


def test_verify_backup_checksum_success_and_failure(temp_backup_env):
    backup_file = temp_backup_env["backup_file"]
    checksum_file = temp_backup_env["checksum_file"]

    # 1. Valid checksum
    assert verify_backup_checksum(backup_file, checksum_file) is True

    # 2. Corrupt / invalid checksum
    bad_checksum_file = temp_backup_env["backup_dir"] / "corrupt.sha256"
    bad_checksum_file.write_text("badhashbadhash  hms_tenant1_20261025.dump.enc\n")
    assert verify_backup_checksum(backup_file, bad_checksum_file) is False

    # 3. Missing checksum file
    missing_checksum = temp_backup_env["backup_dir"] / "nonexistent.sha256"
    assert verify_backup_checksum(backup_file, missing_checksum) is False


def test_decrypt_backup_bytes_roundtrip(temp_backup_env):
    backup_file = temp_backup_env["backup_file"]
    secret_key = temp_backup_env["secret_key"]
    raw_data = temp_backup_env["raw_data"]

    decrypted = decrypt_backup_bytes(backup_file, secret_key)
    assert decrypted == raw_data

    # Decryption with wrong secret key fails
    with pytest.raises(ValueError, match="Failed to decrypt backup artifact"):
        decrypt_backup_bytes(backup_file, "wrong_secret_key")


def test_deliver_backup_offhost_remote_dir(temp_backup_env, monkeypatch):
    backup_file = temp_backup_env["backup_file"]
    checksum_file = temp_backup_env["checksum_file"]
    offhost_dir = temp_backup_env["offhost_dir"]

    # When offhost is disabled
    monkeypatch.setattr(settings, "BACKUP_OFFHOST_ENABLED", False)
    res_disabled = deliver_backup_offhost(backup_file, checksum_file, "tenant1")
    assert res_disabled["delivered"] is False
    assert res_disabled["reason"] == "OFFHOST_DISABLED"

    # When offhost is enabled with remote dir
    monkeypatch.setattr(settings, "BACKUP_OFFHOST_ENABLED", True)
    monkeypatch.setattr(settings, "BACKUP_OFFHOST_REMOTE_DIR", str(offhost_dir))
    monkeypatch.setattr(settings, "BACKUP_OFFHOST_S3_BUCKET", None)

    res = deliver_backup_offhost(backup_file, checksum_file, "tenant1")
    assert res["delivered"] is True
    assert res["destination_type"] == "remote_dir"
    assert res["checksum_verified"] is True

    target_backup = offhost_dir / "tenant1" / backup_file.name
    target_checksum = offhost_dir / "tenant1" / checksum_file.name
    assert target_backup.exists()
    assert target_checksum.exists()
    assert target_backup.read_bytes() == backup_file.read_bytes()


def test_deliver_backup_offhost_s3_configuration(temp_backup_env, monkeypatch):
    backup_file = temp_backup_env["backup_file"]
    checksum_file = temp_backup_env["checksum_file"]

    monkeypatch.setattr(settings, "BACKUP_OFFHOST_ENABLED", True)
    monkeypatch.setattr(settings, "BACKUP_OFFHOST_REMOTE_DIR", None)
    monkeypatch.setattr(settings, "BACKUP_OFFHOST_S3_BUCKET", "avinya-dr-bucket")
    monkeypatch.setattr(settings, "BACKUP_OFFHOST_S3_PREFIX", "dr-backups")

    with patch("boto3.client") as mock_boto_client:
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        res = deliver_backup_offhost(backup_file, checksum_file, "hospital-alpha")
        assert res["delivered"] is True
        assert res["s3_destination"] == f"s3://avinya-dr-bucket/dr-backups/hospital-alpha/{backup_file.name}"
        assert mock_s3.upload_file.call_count == 2


def test_run_restore_drill_end_to_end(temp_backup_env, tmp_path):
    backup_file = temp_backup_env["backup_file"]
    secret_key = temp_backup_env["secret_key"]
    report_file = tmp_path / "drill_report.json"

    # Mock PostgreSQL subprocess and engine calls for isolated test execution
    with patch("backend.scripts.restore_drill.create_engine") as mock_create_engine, \
         patch("backend.scripts.restore_drill.subprocess.run") as mock_subproc, \
         patch("backend.scripts.restore_drill.inspect") as mock_inspect:

        mock_subproc.return_value = MagicMock(returncode=0, stderr=b"")

        # Mock database inspector returning all core tables
        mock_inspector = MagicMock()
        mock_inspector.get_table_names.return_value = [
            "users",
            "patients",
            "patient_encounters",
            "invoices",
            "service_catalog",
            "privacy_policy_revisions",
        ]
        mock_inspect.return_value = mock_inspector

        # Mock Session
        mock_session_inst = MagicMock()
        mock_session_inst.__enter__.return_value = mock_session_inst
        mock_session_inst.execute.return_value.scalar_one.return_value = 42
        mock_session_inst.execute.return_value.scalar_one_or_none.return_value = None

        with patch("backend.scripts.restore_drill.Session", return_value=mock_session_inst):
            report = run_restore_drill(
                backup_file=backup_file,
                tenant_slug="apollo-central-delhi",
                encryption_key=secret_key,
                max_acceptable_rto_seconds=120,
                output_report_path=report_file,
            )

        assert report["tenant_slug"] == "apollo-central-delhi"
        assert report["checksum_verified"] is True
        assert report["sla_rto_passed"] is True
        assert report["verification_status"] == "PASSED"
        assert report["verified_tables_count"] == 6
        assert report["measured_rpo_seconds"] is None
        assert report["missing_critical_tables"] == []
        assert report_file.exists()

        saved_report = json.loads(report_file.read_text(encoding="utf-8"))
        assert saved_report["drill_id"] == report["drill_id"]
        assert saved_report["verification_status"] == "PASSED"
