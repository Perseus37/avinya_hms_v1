import base64
import os
import warnings
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from backend.core.config import settings
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_ENC_V1_PREFIX = "enc:v1:"
_ENC_V2_PREFIX = "enc:v2:"
# SEC-004: Implement PBKDF2HMAC with salt to secure tenant JWT and Backup keys against rotation breaks
# CRYP-001: Enforce strict environment variables for cryptographic salts
_salt_env = getattr(settings, "ENVELOPE_ENCRYPTION_SALT", None)
if not _salt_env or _salt_env == "avinya_secure_salt_2026":
    raise ValueError("CRITICAL: A secure, unique ENVELOPE_ENCRYPTION_SALT must be provided in the environment.")


def _derive_key(secret: str, salt: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt.encode("utf-8"),
        iterations=390000,
    )
    return kdf.derive(secret.encode("utf-8"))


_MASTER_KEY = _derive_key(settings.SECRET_KEY, _salt_env)
_previous_secret = settings.ENVELOPE_ENCRYPTION_PREVIOUS_SECRET_KEY
_previous_salt = settings.ENVELOPE_ENCRYPTION_PREVIOUS_SALT
if bool(_previous_secret) != bool(_previous_salt):
    raise ValueError(
        "Previous envelope encryption secret and salt must be configured together."
    )
_PREVIOUS_KEY = (
    _derive_key(_previous_secret, _previous_salt)
    if _previous_secret and _previous_salt
    else None
)


def encrypt_tenant_secret(plaintext: str) -> str:
    """Encrypts a tenant secret in-memory using AES-256-GCM before storing in the DB."""
    if not plaintext or plaintext.startswith((_ENC_V1_PREFIX, _ENC_V2_PREFIX)):
        return plaintext
    aesgcm = AESGCM(_MASTER_KEY)
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
    try:
        _decrypt_payload(value, _ENC_V2_PREFIX, _MASTER_KEY)
    except InvalidTag:
        return True
    return False


def decrypt_tenant_secret(value: str) -> str:
    """Decrypts a tenant secret from the DB in microseconds with zero network calls."""
    if not value:
        return ""
    if value.startswith(_ENC_V1_PREFIX):
        prefix = _ENC_V1_PREFIX
        keys = [_PREVIOUS_KEY, _MASTER_KEY]
    elif value.startswith(_ENC_V2_PREFIX):
        prefix = _ENC_V2_PREFIX
        keys = [_MASTER_KEY, _PREVIOUS_KEY]
    else:
        warnings.warn(
            "A legacy unencrypted tenant secret was loaded; run the envelope-key rotation tool.",
            RuntimeWarning,
            stacklevel=2,
        )
        return str(value)
    last_error = None
    for key in dict.fromkeys(keys):
        if key is None:
            continue
        try:
            return _decrypt_payload(value, prefix, key)
        except InvalidTag as exc:
            last_error = exc
    raise ValueError("Tenant secret could not be decrypted with configured keys.") from last_error