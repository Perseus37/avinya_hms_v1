import importlib.util
from pathlib import Path
import sys

import pytest


LOAD_TEST_PATH = Path(__file__).parent / "load" / "tenant_read_load.py"
SPEC = importlib.util.spec_from_file_location("tenant_read_load", LOAD_TEST_PATH)
assert SPEC and SPEC.loader
load_profile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = load_profile
SPEC.loader.exec_module(load_profile)


def test_load_profile_requires_requested_test_accounts_per_tenant(tmp_path):
    user_file = tmp_path / "users.csv"
    user_file.write_text(
        "tenant,base_url,email,password\n"
        "hospital-a,https://a.example.test,user1@example.test,secret1\n"
        "hospital-a,https://a.example.test,user2@example.test,secret2\n"
        "hospital-b,https://b.example.test,user3@example.test,secret3\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hospital-b"):
        load_profile.load_test_users(user_file, users_per_tenant=2)


def test_load_profile_selects_exact_account_count_per_tenant(tmp_path):
    user_file = tmp_path / "users.csv"
    user_file.write_text(
        "tenant,base_url,email,password\n"
        "hospital-a,https://a.example.test,a1@example.test,secret1\n"
        "hospital-a,https://a.example.test,a2@example.test,secret2\n"
        "hospital-a,https://a.example.test,a3@example.test,secret3\n"
        "hospital-b,https://b.example.test,b1@example.test,secret4\n"
        "hospital-b,https://b.example.test,b2@example.test,secret5\n",
        encoding="utf-8",
    )

    selected = load_profile.load_test_users(user_file, users_per_tenant=2)

    assert len(selected) == 4
    assert [user.tenant for user in selected] == [
        "hospital-a",
        "hospital-a",
        "hospital-b",
        "hospital-b",
    ]

    with pytest.raises(ValueError, match="Expected 10 tenants"):
        load_profile.load_test_users(
            user_file, users_per_tenant=2, expected_tenants=10
        )
