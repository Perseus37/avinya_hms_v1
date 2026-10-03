# security/envelope.py
import base64
import os
from typing import Dict
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import requests

KMS_URL = os.getenv("INTERNAL_KMS_URL", "").strip()
KMS_TOKEN = os.getenv("KMS_INTERNAL_AUTH_TOKEN", "").strip()

_DECRYPT_CACHE: Dict[str, str] = {}
_LOCAL_FALLBACK_KEK = os.getenv(
    "KMS_INTERNAL_ROOT_KEY",
    os.getenv("SECRET_KEY", "avinya_local_dev_fallback_kek_32b"),
).encode("utf-8")[:32].ljust(32, b"0")

_ENC_PREFIX = "enc:v1:"


def encrypt_tenant_secret(plaintext: str) -> str:
    if not plaintext:
        raise ValueError("Cannot encrypt an empty secret.")
    if plaintext.startswith(_ENC_PREFIX):
        return plaintext

    if KMS_URL:
        response = requests.post(
            f"{KMS_URL}/transit/encrypt",
            headers={"X-KMS-Auth": KMS_TOKEN},
            json={"data": plaintext},
            timeout=2.0,
        )
        response.raise_for_status()
        return f"{_ENC_PREFIX}{response.json()['result']}"

    aesgcm = AESGCM(_LOCAL_FALLBACK_KEK)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    b64 = base64.b64encode(nonce + ct).decode("utf-8")
    return f"{_ENC_PREFIX}{b64}"


def decrypt_tenant_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_ENC_PREFIX):
        return str(value)

    if value in _DECRYPT_CACHE:
        return _DECRYPT_CACHE[value]

    ciphertext_b64 = value[len(_ENC_PREFIX):]

    if KMS_URL:
        response = requests.post(
            f"{KMS_URL}/transit/decrypt",
            headers={"X-KMS-Auth": KMS_TOKEN},
            json={"data": ciphertext_b64},
            timeout=2.0,
        )
        response.raise_for_status()
        plaintext = str(response.json()["result"])
    else:
        raw = base64.b64decode(ciphertext_b64.encode("utf-8"))
        nonce, ct = raw[:12], raw[12:]
        aesgcm = AESGCM(_LOCAL_FALLBACK_KEK)
        plaintext = aesgcm.decrypt(nonce, ct, None).decode("utf-8")

    _DECRYPT_CACHE[value] = plaintext
    return plaintext