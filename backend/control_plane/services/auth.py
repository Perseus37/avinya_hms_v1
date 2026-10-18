import secrets
from typing import Optional
from fastapi import Header, HTTPException, status
from backend.core.config import settings

def verify_control_plane_secret(
    x_control_plane_secret: Optional[str] = Header(default=None, alias="X-Control-Plane-Secret"),
) -> None:
    expected_key = getattr(settings, "CONTROL_PLANE_MASTER_KEY", None)
    if expected_key and (not x_control_plane_secret or not secrets.compare_digest(x_control_plane_secret, expected_key)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Control Plane credentials.")

def _verify_vendor_key(provided_key: str) -> None:
    if not provided_key or not secrets.compare_digest(provided_key, settings.MASTER_ONBOARDING_KEY):
        raise HTTPException(status_code=403, detail="Invalid Master Vendor Setup Key.")