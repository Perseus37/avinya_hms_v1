import base64
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Optional


def totp_code(secret: str, at: Optional[datetime] = None) -> str:
    timestamp = (at or datetime.now(timezone.utc)).timestamp()
    counter = int(timestamp // 30)
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def matching_totp_counter(secret: str, code: str, at: Optional[datetime] = None) -> Optional[int]:
    now = at or datetime.now(timezone.utc)
    current_counter = int(now.timestamp() // 30)
    for counter in range(current_counter - 1, current_counter + 2):
        candidate = totp_code(secret, datetime.fromtimestamp(counter * 30, timezone.utc))
        if hmac.compare_digest(candidate, code):
            return counter
    return None
