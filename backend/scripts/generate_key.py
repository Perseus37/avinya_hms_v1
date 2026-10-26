"""Generate cryptographically secure 256-bit entropy keys for .env configuration."""

import secrets
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BACKEND_DIR.parent
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))


def generate_keys() -> dict[str, str]:
    return {
        "SECRET_KEY": secrets.token_hex(32),
        "MASTER_ONBOARDING_KEY": secrets.token_hex(32),
        "ENVELOPE_ENCRYPTION_SALT": secrets.token_hex(16),
        "BACKUP_ENCRYPTION_SALT": secrets.token_hex(16),
    }


def main() -> None:
    keys = generate_keys()
    for key, val in keys.items():
        print(f"Generated {key}: {val}")


if __name__ == "__main__":
    main()

