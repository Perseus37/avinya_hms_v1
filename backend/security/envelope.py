import base64
import hashlib
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from config import settings

_ENC_PREFIX = "enc:v1:"

# Derive a deterministic 32-byte AES-256 key directly from settings.SECRET_KEY
_MASTER_KEY = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()


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