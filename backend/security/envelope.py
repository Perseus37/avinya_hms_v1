import base64
import hashlib
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from config import settings
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_ENC_PREFIX = "enc:v1:"    
# SEC-004: Implement PBKDF2HMAC with salt to secure tenant JWT and Backup keys against rotation breaks
# CRYP-001: Enforce strict environment variables for cryptographic salts
_salt_env = getattr(settings, "ENVELOPE_ENCRYPTION_SALT", None)
if not _salt_env or _salt_env == "avinya_secure_salt_2026":
    raise ValueError("CRITICAL: A secure, unique ENVELOPE_ENCRYPTION_SALT must be provided in the environment.")
_salt = _salt_env.encode("utf-8")
_kdf = PBKDF2HMAC(
    algorithm=hashes.SHA256(),
    length=32,
    salt=_salt,
    iterations=390000,
)
_MASTER_KEY = _kdf.derive(settings.SECRET_KEY.encode("utf-8"))


def encrypt_tenant_secret(plaintext: str) -> str:
    """Encrypts a tenant secret in-memory using AES-256-GCM before storing in the DB."""
    if not plaintext or plaintext.startswith(_ENC_PREFIX):
        return plaintext
    aesgcm = AESGCM(_MASTER_KEY)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return f"{_ENC_PREFIX}{base64.b64encode(nonce + ct).decode('utf-8')}"


def decrypt_tenant_secret(value: str) -> str:
    """Decrypts a tenant secret from the DB in microseconds with zero network calls."""
    if not value or not value.startswith(_ENC_PREFIX):
        return str(value or "")
    raw = base64.b64decode(value[len(_ENC_PREFIX):].encode("utf-8"))
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(_MASTER_KEY)
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")