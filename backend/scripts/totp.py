"""Generate RFC 6238 6-digit TOTP codes for MFA enrollment and operator sign-in."""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BACKEND_DIR.parent
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))

from sqlalchemy import select
from backend.core.config import settings
from backend.core.database import ControlSessionLocal
from backend.control_plane.models import ControlPlaneOperator
from backend.security.envelope import decrypt_tenant_secret
from backend.security.totp import totp_code


def get_code_for_secret(secret: str) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    seconds_remaining = 30 - int(now.timestamp() % 30)
    code = totp_code(secret.strip(), now)
    return code, seconds_remaining


def get_code_for_operator(email: str) -> tuple[str, int]:
    clean_email = email.strip().lower()
    with ControlSessionLocal() as db:
        operator = db.execute(
            select(ControlPlaneOperator).where(ControlPlaneOperator.email == clean_email)
        ).scalar_one_or_none()
        if not operator:
            raise ValueError(f"No operator found with email '{clean_email}'.")
        if not operator.mfa_secret:
            raise ValueError(
                f"Operator '{clean_email}' has not initialized MFA yet. "
                "Sign in through the web console and copy the 'Authenticator secret' shown on screen."
            )
        secret = decrypt_tenant_secret(operator.mfa_secret)
        return get_code_for_secret(secret)


def main(argv: list[str] | None = None) -> int:
    # N-63: Prohibit TOTP utility execution in production environments
    if settings.is_production() or settings.ENVIRONMENT.lower() == "production":
        print(
            "Security Guard: totp.py is a developer/testing utility and is strictly disabled in production environments.",
            file=sys.stderr,
        )
        return 1

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "secret",
        nargs="?",
        default=None,
        help="Base32 authenticator secret displayed on the MFA enrollment screen",
    )
    parser.add_argument(
        "--operator",
        default=None,
        help="Operator email (looks up enrolled MFA secret from the control plane database)",
    )
    args = parser.parse_args(argv)

    try:
        if args.secret:
            code, remaining = get_code_for_secret(args.secret)
            print(f"Current 6-Digit Authenticator Code: {code} (valid for {remaining}s)")
            return 0
        elif args.operator:
            code, remaining = get_code_for_operator(args.operator)
            print(f"Operator '{args.operator}' Authenticator Code: {code} (valid for {remaining}s)")
            return 0
        else:
            parser.error("Must provide either an authenticator secret or --operator <email>.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

