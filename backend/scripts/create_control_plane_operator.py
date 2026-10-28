"""Create a named control-plane operator after applying control-plane migrations."""

import argparse
import getpass
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BACKEND_DIR.parent
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from backend.core.database import ControlSessionLocal
from backend.control_plane.models import ControlPlaneOperator
from backend.domains.identity.passwords import pwd_context


def _validate_password(password: str) -> None:
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Password must contain 12 to 128 characters.")
    classes = (
        any(char.islower() for char in password),
        any(char.isupper() for char in password),
        any(char.isdigit() for char in password),
        any(not char.isalnum() for char in password),
    )
    if sum(classes) < 3:
        raise ValueError("Password must use at least three character classes.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Operator corporate email address")
    parser.add_argument(
        "--name",
        default=None,
        help="Operator full display name (defaults to formatted email username if omitted)",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Operator password (if omitted, prompts interactively via getpass)",
    )
    parser.add_argument(
        "--role",
        default="viewer",
        choices=["superadmin", "ops", "support", "billing", "viewer"],
        help="Operator RBAC role (default: viewer)",
    )
    args = parser.parse_args(argv)
    raw_email = args.email.strip().lower()
    try:
        email = str(TypeAdapter(EmailStr).validate_python(raw_email))
    except Exception:
        parser.error(
            f"'{raw_email}' is not a valid RFC-compliant email address. "
            "Please use a standard domain (e.g. admin@avinya-hms.com or admin@example.org)."
        )

    full_name = (
        args.name.strip()
        if args.name and args.name.strip()
        else email.split("@")[0].replace(".", " ").title()
    ) or "Administrator"

    if args.password is not None:
        from backend.core.config import settings

        if settings.is_production() or settings.ENVIRONMENT.lower() in {"production", "prod", "staging"}:
            parser.error(
                "Security Guard: Supplying raw passwords via the --password command-line argument is strictly prohibited in production and staging environments. "
                "Omit --password to provide the credential securely via interactive masked getpass prompt."
            )
        password = args.password
        try:
            _validate_password(password)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        password = getpass.getpass("Operator password: ")
        confirmation = getpass.getpass("Confirm operator password: ")
        if password != confirmation:
            parser.error("Passwords do not match.")
        try:
            _validate_password(password)
        except ValueError as exc:
            parser.error(str(exc))

    try:
        with ControlSessionLocal() as db:
            if db.execute(
                select(ControlPlaneOperator.id).where(
                    ControlPlaneOperator.email == email
                )
            ).scalar_one_or_none():
                parser.error("An operator with that email already exists.")
            db.add(
                ControlPlaneOperator(
                    email=email,
                    full_name=full_name,
                    role=args.role,
                    password_hash=pwd_context.hash(password),
                )
            )
            db.commit()
    except OperationalError:
        print(
            "Could not create operator; verify the control-plane database is available "
            "and its Alembic migrations have been applied.",
            file=sys.stderr,
        )
        return 1
    print(f"Created operator {email} ('{full_name}') with role '{args.role}'; MFA enrollment is required at first sign-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
