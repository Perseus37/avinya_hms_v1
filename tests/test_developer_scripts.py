"""Unit tests for developer and operator CLI scripts."""

import pytest
from backend.scripts.generate_key import generate_keys
from backend.scripts.create_control_plane_operator import _validate_password, main as operator_main
from backend.scripts.bootstrap import main as bootstrap_main
from backend.scripts.migrate import main as migrate_main
from backend.scripts.seed import main as seed_main


def test_generate_key_entropy_and_lengths():
    keys = generate_keys()
    assert "SECRET_KEY" in keys
    assert "MASTER_ONBOARDING_KEY" in keys
    assert "ENVELOPE_ENCRYPTION_SALT" in keys
    assert "BACKUP_ENCRYPTION_SALT" in keys

    # SECRET_KEY and MASTER_ONBOARDING_KEY must be 64-character hex strings (32 bytes = 256 bits)
    assert len(keys["SECRET_KEY"]) == 64
    assert len(keys["MASTER_ONBOARDING_KEY"]) == 64
    assert len(keys["ENVELOPE_ENCRYPTION_SALT"]) == 32
    assert len(keys["BACKUP_ENCRYPTION_SALT"]) == 32

    # Keys must be distinct
    assert keys["SECRET_KEY"] != keys["MASTER_ONBOARDING_KEY"]
    assert keys["ENVELOPE_ENCRYPTION_SALT"] != keys["BACKUP_ENCRYPTION_SALT"]


def test_create_control_plane_operator_password_validation():
    # Less than 12 chars -> error
    with pytest.raises(ValueError, match="12 to 128 characters"):
        _validate_password("Short1!")

    # Less than 3 character classes -> error
    with pytest.raises(ValueError, match="three character classes"):
        _validate_password("alllowercaseletters123")

    # Valid complex password
    _validate_password("AdminPassword123!")


def test_create_control_plane_operator_cli_args(monkeypatch):
    # Test valid CLI execution without --name (defaults to username formatted)
    # Mock database session to verify arguments and prevent DB writes
    from unittest.mock import MagicMock, patch
    from backend.control_plane.models import ControlPlaneOperator

    mock_db = MagicMock()
    # No existing operator with that email
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    with patch("backend.scripts.create_control_plane_operator.ControlSessionLocal") as mock_session_ctx:
        mock_session_ctx.return_value.__enter__.return_value = mock_db

        ret = operator_main([
            "--email", "admin@avinya-hms.com",
            "--password", "AdminPassword123!",
            "--role", "superadmin"
        ])
        assert ret == 0

        # Verify an operator was added with full_name defaulted from email
        added_operator = mock_db.add.call_args[0][0]
        assert isinstance(added_operator, ControlPlaneOperator)
        assert added_operator.email == "admin@avinya-hms.com"
        assert added_operator.full_name == "Admin"
        assert added_operator.role == "superadmin"


def test_create_control_plane_operator_rfc_email_validation():
    # .local is reserved and rejected by EmailStr
    with pytest.raises(SystemExit):
        operator_main([
            "--email", "invalid@avinya.local",
            "--password", "AdminPassword123!",
            "--role", "superadmin"
        ])


def test_create_control_plane_operator_duplicate_rejection():
    from unittest.mock import MagicMock, patch

    mock_db = MagicMock()
    # Existing operator ID returned
    mock_db.execute.return_value.scalar_one_or_none.return_value = 1

    with patch("backend.scripts.create_control_plane_operator.ControlSessionLocal") as mock_session_ctx:
        mock_session_ctx.return_value.__enter__.return_value = mock_db

        with pytest.raises(SystemExit):
            operator_main([
                "--email", "existing@avinya.local",
                "--password", "AdminPassword123!",
                "--role", "ops"
            ])


def test_migrate_cli_accepts_all_tenants_alias(monkeypatch):
    from unittest.mock import patch

    with patch("backend.scripts.migrate.migrate_remaining_fleet") as mock_fleet:
        mock_fleet.return_value = {"upgraded": [], "failed": []}
        ret = migrate_main(["--all-tenants"])
        assert ret == 0
        assert mock_fleet.called


def test_bootstrap_reset_guard_in_production(monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="strictly disallowed in production"):
        bootstrap_main(["--reset"])

