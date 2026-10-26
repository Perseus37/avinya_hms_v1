"""Create a named control-plane operator after applying control-plane migrations."""

import argparse
import getpass
import sys

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--role",
        default="superadmin",
        choices=["superadmin", "ops", "support", "billing", "viewer"],
        help="Operator RBAC role (default: superadmin)",
    )
    args = parser.parse_args()
    email = args.email.strip().lower()
    if "@" not in email or len(email) > 255:
        parser.error("--email must be a valid email address.")

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
                    full_name=args.name.strip(),
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
    print(f"Created operator {email} with role '{args.role}'; MFA enrollment is required at first sign-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
