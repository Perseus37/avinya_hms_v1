import base64
import os
from pathlib import Path
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel

app = FastAPI(title="Avinya Internal KMS", docs_url=None, redoc_url=None)


def _load_root_kek() -> bytes:
    # 1. Prefer memory-mounted Docker secret
    secret_file = Path("/run/secrets/root_kek")
    if secret_file.is_file():
        raw = secret_file.read_bytes().strip()
        if raw:
            return raw[:32].ljust(32, b"0")

    # 2. Fallback to container environment variable
    raw_env = os.getenv("KMS_INTERNAL_ROOT_KEY", "").strip()
    if not raw_env:
        raise RuntimeError("KMS Root Key missing. Cannot initialize cryptosystem.")
    return raw_env.encode("utf-8")[:32].ljust(32, b"0")


ROOT_KEK = _load_root_kek()
INTERNAL_AUTH_TOKEN = os.getenv("KMS_INTERNAL_AUTH_TOKEN", "")


def verify_internal_kms_token(x_kms_auth: str = Header(..., alias="X-KMS-Auth")) -> None:
    if not INTERNAL_AUTH_TOKEN or not secrets.compare_digest(x_kms_auth, INTERNAL_AUTH_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized internal KMS access attempt.",
        )


class TransitRequest(BaseModel):
    data: str


class TransitResponse(BaseModel):
    result: str


@app.get("/health")
def health_check():
    return {"status": "HEALTHY", "service": "hms-kms"}


@app.post("/transit/encrypt", response_model=TransitResponse, dependencies=[Depends(verify_internal_kms_token)])
def encrypt_secret(payload: TransitRequest):
    aesgcm = AESGCM(ROOT_KEK)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, payload.data.encode("utf-8"), None)
    encoded = base64.b64encode(nonce + ciphertext).decode("utf-8")
    return {"result": encoded}


@app.post("/transit/decrypt", response_model=TransitResponse, dependencies=[Depends(verify_internal_kms_token)])
def decrypt_secret(payload: TransitRequest):
    try:
        raw = base64.b64decode(payload.data.encode("utf-8"))
        nonce, ciphertext = raw[:12], raw[12:]
        aesgcm = AESGCM(ROOT_KEK)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
        return {"result": plaintext}
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decryption failed: corrupted ciphertext or key mismatch.",
        )