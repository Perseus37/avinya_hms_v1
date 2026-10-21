import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from backend.core.config import settings
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_ENC_V1_PREFIX = "enc:v1:"
_ENC_V2_PREFIX = "enc:v2:"

_MASTER_KEY_CACHED: bytes | None = None
_PREVIOUS_KEY_CACHED: bytes | None = None


def _derive_key(secret: str, salt: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt.encode("utf-8"),
        iterations=390000,
    )
    return kdf.derive(secret.encode("utf-8"))


def _get_master_key() -> bytes:
    global _MASTER_KEY_CACHED
    if "_MASTER_KEY" in globals():
        return globals()["_MASTER_KEY"]
    if _MASTER_KEY_CACHED is None:
        salt_env = getattr(settings, "ENVELOPE_ENCRYPTION_SALT", None)
        if not salt_env or salt_env == "avinya_secure_salt_2026":
            raise ValueError(
                "CRITICAL: A secure, unique ENVELOPE_ENCRYPTION_SALT must be provided in the environment."
            )
        _MASTER_KEY_CACHED = _derive_key(settings.SECRET_KEY, salt_env)
    return _MASTER_KEY_CACHED


def _get_previous_key() -> bytes | None:
    global _PREVIOUS_KEY_CACHED
    if "_PREVIOUS_KEY" in globals():
        return globals()["_PREVIOUS_KEY"]
    if _PREVIOUS_KEY_CACHED is None:
        previous_secret = settings.ENVELOPE_ENCRYPTION_PREVIOUS_SECRET_KEY
        previous_salt = settings.ENVELOPE_ENCRYPTION_PREVIOUS_SALT
        if bool(previous_secret) != bool(previous_salt):
            raise ValueError(
                "Previous envelope encryption secret and salt must be configured together."
            )
        if previous_secret and previous_salt:
            _PREVIOUS_KEY_CACHED = _derive_key(previous_secret, previous_salt)
    return _PREVIOUS_KEY_CACHED


def __getattr__(name: str):
    if name == "_MASTER_KEY":
        return _get_master_key()
    if name == "_PREVIOUS_KEY":
        return _get_previous_key()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def encrypt_tenant_secret(plaintext: str) -> str:
    """Encrypts a tenant secret in-memory using AES-256-GCM before storing in the DB."""
    if not plaintext or plaintext.startswith((_ENC_V1_PREFIX, _ENC_V2_PREFIX)):
        return plaintext
    master_key = _get_master_key()
    aesgcm = AESGCM(master_key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return f"{_ENC_V2_PREFIX}{base64.b64encode(nonce + ct).decode('utf-8')}"


def _decrypt_payload(value: str, prefix: str, key: bytes) -> str:
    raw = base64.b64decode(value[len(prefix):].encode("utf-8"), validate=True)
    if len(raw) < 12 + 16:
        raise ValueError("Encrypted tenant secret has an invalid payload.")
    nonce, ciphertext = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")


def needs_reencryption(value: str) -> bool:
    if not value or not value.startswith(_ENC_V2_PREFIX):
        return bool(value)
    master_key = _get_master_key()
    try:
        _decrypt_payload(value, _ENC_V2_PREFIX, master_key)
    except InvalidTag:
        return True
    return False


def decrypt_tenant_secret(value: str) -> str:
    """Decrypts a tenant secret from the DB in microseconds with zero network calls."""
    if not value:
        raise ValueError("Tenant secret is missing.")
    master_key = _get_master_key()
    previous_key = _get_previous_key()
    if value.startswith(_ENC_V1_PREFIX):
        prefix = _ENC_V1_PREFIX
        keys = [previous_key, master_key]
    elif value.startswith(_ENC_V2_PREFIX):
        prefix = _ENC_V2_PREFIX
        keys = [master_key, previous_key]
    else:
        raise ValueError(
            "Tenant secret is stored as plaintext; run the envelope-key rotation tool before use."
        )
    last_error = None
    for key in dict.fromkeys(keys):
        if key is None:
            continue
        try:
            return _decrypt_payload(value, prefix, key)
        except InvalidTag as exc:
            last_error = exc
    raise ValueError("Tenant secret could not be decrypted with configured keys.") from last_error
