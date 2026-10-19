import base64
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.security import envelope


def _encrypt_v1(plaintext: str, key: bytes) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return "enc:v1:" + base64.b64encode(nonce + ciphertext).decode("utf-8")


def test_envelope_encryption_uses_versioned_authenticated_ciphertext():
    encrypted = envelope.encrypt_tenant_secret("sensitive-value")

    assert encrypted.startswith("enc:v2:")
    assert envelope.decrypt_tenant_secret(encrypted) == "sensitive-value"


def test_envelope_rotation_reads_previous_version_and_reencrypts(monkeypatch):
    previous_key = envelope._MASTER_KEY
    current_key = os.urandom(32)
    legacy_value = _encrypt_v1("rotation-value", previous_key)
    monkeypatch.setattr(envelope, "_PREVIOUS_KEY", previous_key)
    monkeypatch.setattr(envelope, "_MASTER_KEY", current_key)

    plaintext = envelope.decrypt_tenant_secret(legacy_value)
    encrypted = envelope.encrypt_tenant_secret(plaintext)

    assert plaintext == "rotation-value"
    assert encrypted.startswith("enc:v2:")
    assert envelope.decrypt_tenant_secret(encrypted) == "rotation-value"


def test_legacy_plaintext_secret_fails_closed_until_rotated():
    with pytest.raises(ValueError, match="stored as plaintext"):
        envelope.decrypt_tenant_secret("legacy-value")


def test_missing_tenant_secret_fails_closed():
    with pytest.raises(ValueError, match="Tenant secret is missing"):
        envelope.decrypt_tenant_secret("")


def test_rotation_script_reencrypts_legacy_tenant_secrets(
    multi_tenant_env, monkeypatch
):
    from backend.control_plane.models import TenantRegistry
    from backend.scripts import rotate_envelope_keys

    factory = multi_tenant_env["control_session_factory"]
    monkeypatch.setattr(rotate_envelope_keys, "ControlSessionLocal", factory)
    with factory() as db:
        tenant = db.query(TenantRegistry).filter_by(subdomain_slug="default").one()
        tenant.jwt_secret_key = "legacy-jwt-value"
        tenant.backup_encryption_key = _encrypt_v1(
            "legacy-backup-value", envelope._MASTER_KEY
        )
        tenant.hl7_shared_secret_encrypted = "legacy-hl7-value"
        db.commit()

    assert rotate_envelope_keys.rotate_tenant_secrets() == 3
    assert rotate_envelope_keys.rotate_tenant_secrets() == 0

    with factory() as db:
        tenant = db.query(TenantRegistry).filter_by(subdomain_slug="default").one()
        assert tenant.jwt_secret_key.startswith("enc:v2:")
        assert tenant.backup_encryption_key.startswith("enc:v2:")
        assert tenant.hl7_shared_secret_encrypted.startswith("enc:v2:")
        assert envelope.decrypt_tenant_secret(tenant.jwt_secret_key) == "legacy-jwt-value"
        assert envelope.decrypt_tenant_secret(tenant.backup_encryption_key) == "legacy-backup-value"
        assert envelope.decrypt_tenant_secret(tenant.hl7_shared_secret_encrypted) == "legacy-hl7-value"
