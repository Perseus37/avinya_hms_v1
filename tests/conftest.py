from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from jose import jwt
from passlib.context import CryptContext
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from control_models import ControlBase, DeploymentModeEnum, TenantRegistry
import database
from database import Base, get_control_db, get_redis
from main import app, v1_app
from models import (
    Department,
    DoctorProfile,
    GenderEnum,
    Role,
    User,
    UserRole,
    UserStatusEnum,
)
import seed
from seed import seed_universal_hospital_data

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Pre-compute bcrypt hashes ONCE per test session so fixtures run in milliseconds
_HASH_CACHE: dict[str, str] = {
    "AdminPassword123!": pwd_context.hash("AdminPassword123!"),
    "StaffPass123!": pwd_context.hash("StaffPass123!"),
}
_orig_hash = pwd_context.hash


def _fast_cached_hash(secret: str, *args, **kwargs) -> str:
    if secret not in _HASH_CACHE:
        _HASH_CACHE[secret] = _orig_hash(secret, *args, **kwargs)
    return _HASH_CACHE[secret]


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value

    def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value

    def delete(self, key: str):
        self.store.pop(key, None)

    def ping(self) -> bool:
        return True

    def clear(self):
        self.store.clear()


@pytest.fixture()
def fake_redis():
    r = FakeRedis()
    yield r
    r.clear()


@pytest.fixture()
def multi_tenant_env(monkeypatch, fake_redis):
    monkeypatch.setattr(seed.pwd_context, "hash", _fast_cached_hash)

    control_eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ControlBase.metadata.create_all(bind=control_eng)
    TestControlSession = sessionmaker(autocommit=False, autoflush=False, bind=control_eng)

    tenant_engines = {}
    for db_name in ("hms_default", "hms_apollo", "hms_sunrise"):
        t_eng = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=t_eng)
        tenant_engines[db_name] = t_eng

    monkeypatch.setattr(database, "ControlSessionLocal", TestControlSession)
    monkeypatch.setattr(database.tenant_engine_pool, "get_engine", lambda db_name: tenant_engines[db_name])

    def override_get_control_db():
        db = TestControlSession()
        try:
            yield db
        finally:
            db.close()

    for target_app in (app, v1_app):
            target_app.dependency_overrides[get_control_db] = override_get_control_db
            target_app.dependency_overrides[get_redis] = lambda: fake_redis

    with TestControlSession() as cdb:
        cdb.add_all([
            TenantRegistry(
                hospital_name="Avinya Default Hospital",
                branch_code="HQ-HYD-01",
                subdomain_slug="default",
                db_name="hms_default",
                admin_email="admin@default.com",
                jwt_secret_key="default_jwt_secret_key_32_chars_minimum_len",
                backup_encryption_key="default_backup_key_32_chars_minimum_len",
                deployment_mode=DeploymentModeEnum.CLOUD,
                is_active=True,
            ),
            TenantRegistry(
                hospital_name="Apollo Hospital",
                branch_code="APOLLO-HYD-01",
                subdomain_slug="apollo",
                db_name="hms_apollo",
                admin_email="admin@apollo.com",
                jwt_secret_key="apollo_jwt_secret_key_32_chars_minimum_len!",
                backup_encryption_key="apollo_backup_key_32_chars_minimum_len!",
                deployment_mode=DeploymentModeEnum.CLOUD,
                is_active=True,
            ),
            TenantRegistry(
                hospital_name="Sunrise Hospital",
                branch_code="SUN-BLR-01",
                subdomain_slug="sunrise",
                db_name="hms_sunrise",
                admin_email="admin@sunrise.com",
                jwt_secret_key="sunrise_jwt_secret_key_32_chars_minimum_len",
                backup_encryption_key="sunrise_backup_key_32_chars_minimum_len",
                deployment_mode=DeploymentModeEnum.CLOUD,
                is_active=True,
            ),
            TenantRegistry(
                hospital_name="Suspended Hospital",
                branch_code="SUSP-01",
                subdomain_slug="suspended",
                db_name="hms_suspended",
                admin_email="admin@suspended.com",
                jwt_secret_key="suspended_jwt_secret_32_chars_minimum_len",
                backup_encryption_key="suspended_backup_key_32_chars_minimum",
                deployment_mode=DeploymentModeEnum.CLOUD,
                is_active=False,
            ),
            TenantRegistry(
                hospital_name="OnPrem Local Hospital",
                branch_code="ONPREM-01",
                subdomain_slug="onprem",
                db_name="hms_onprem",
                admin_email="admin@onprem.com",
                jwt_secret_key="onprem_jwt_secret_key_32_chars_minimum_len",
                backup_encryption_key="onprem_backup_key_32_chars_minimum_len",
                deployment_mode=DeploymentModeEnum.ON_PREM,
                is_active=True,
            ),
        ])
        cdb.commit()

    tenant_configs = [
        ("hms_default", "Avinya Default Hospital", "HQ-HYD-01", "admin@default.com"),
        ("hms_apollo", "Apollo Hospital", "APOLLO-HYD-01", "admin@apollo.com"),
        ("hms_sunrise", "Sunrise Hospital", "SUN-BLR-01", "admin@sunrise.com"),
    ]
    for db_name, h_name, b_code, a_email in tenant_configs:
        TSession = sessionmaker(autocommit=False, autoflush=False, bind=tenant_engines[db_name])
        with TSession() as tdb:
            seed_universal_hospital_data(
                db=tdb,
                hospital_name=h_name,
                branch_code=b_code,
                admin_email=a_email,
                admin_password="AdminPassword123!",
            )

    DefaultSession = sessionmaker(autocommit=False, autoflush=False, bind=tenant_engines["hms_default"])
    with DefaultSession() as ddb:
        dept = ddb.query(Department).filter(Department.code == "DEPT_GEN_MED").first()
        roles = {r.code: r for r in ddb.query(Role).all()}

        staff_specs = [
            ("DOC001", "doctor@default.com", "Dr. Gregory House", "ROLE_DOCTOR"),
            ("NUR001", "nurse@default.com", "Nurse Clara Barton", "ROLE_NURSE"),
            ("PHM001", "pharmacist@default.com", "Pharm. Alexander Fleming", "ROLE_PHARMACIST"),
            ("BIL001", "billing@default.com", "Billing Exec Luca Pacioli", "ROLE_BILLING"),
            ("AUD001", "auditor@default.com", "Auditor ReadOnly", "ROLE_AUDITOR_READONLY"),
        ]
        for login_id, email, full_name, role_code in staff_specs:
            u = User(
                login_id=login_id,
                email=email,
                hashed_password=_fast_cached_hash("StaffPass123!"),
                full_name=full_name,
                gender=GenderEnum.OTHER,
                status=UserStatusEnum.ACTIVE,
                is_superuser=False,
            )
            ddb.add(u)
            ddb.flush()
            ddb.add(UserRole(user_id=u.id, role_id=roles[role_code].id, department_id=dept.id))

            if role_code == "ROLE_DOCTOR":
                ddb.add(
                    DoctorProfile(
                        user_id=u.id,
                        clinical_id="CLIN-DOC-001",
                        department_id=dept.id,
                        qualification="MD Internal Medicine",
                        specialization="General Medicine",
                        medical_council_name="NMC",
                        medical_registration_number="NMC-2026-001",
                        registration_valid_till=datetime(2032, 12, 31, tzinfo=timezone.utc),
                        consultation_fee_paise=50000,
                        consultation_duration_minutes=15,
                    )
                )
        ddb.commit()

    yield {
        "control_session_factory": TestControlSession,
        "tenant_engines": tenant_engines,
        "redis": fake_redis,
    }

    app.dependency_overrides.clear()
    v1_app.dependency_overrides.clear()
    
    for eng in tenant_engines.values():
        Base.metadata.drop_all(bind=eng)
        eng.dispose()
    ControlBase.metadata.drop_all(bind=control_eng)
    control_eng.dispose()


@pytest.fixture()
def client(multi_tenant_env):
    with TestClient(app) as test_client:
        yield test_client


def _mint_test_headers(
    multi_tenant_env,
    subdomain_slug: str,
    email: str,
    jwt_secret: str,
    branch_code: str,
) -> dict[str, str]:
    db_name = f"hms_{subdomain_slug}"
    TSession = sessionmaker(bind=multi_tenant_env["tenant_engines"][db_name])
    with TSession() as db:
        user = db.query(User).filter(User.email == email).one()
        expire = datetime.now(timezone.utc) + timedelta(minutes=30)
        token = jwt.encode(
            {
                "sub": str(user.id),
                "tenant_slug": subdomain_slug,
                "branch_code": branch_code,
                "exp": expire,
            },
            jwt_secret,
            algorithm=settings.ALGORITHM,
        )
        return {
            "Authorization": f"Bearer {token}",
            "X-Hospital-Subdomain": subdomain_slug,
        }


@pytest.fixture()
def superuser_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "admin@default.com",
        "default_jwt_secret_key_32_chars_minimum_len",
        "HQ-HYD-01",
    )


@pytest.fixture()
def doctor_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "doctor@default.com",
        "default_jwt_secret_key_32_chars_minimum_len",
        "HQ-HYD-01",
    )


@pytest.fixture()
def nurse_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "nurse@default.com",
        "default_jwt_secret_key_32_chars_minimum_len",
        "HQ-HYD-01",
    )


@pytest.fixture()
def pharmacist_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "pharmacist@default.com",
        "default_jwt_secret_key_32_chars_minimum_len",
        "HQ-HYD-01",
    )


@pytest.fixture()
def billing_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "billing@default.com",
        "default_jwt_secret_key_32_chars_minimum_len",
        "HQ-HYD-01",
    )


@pytest.fixture()
def auditor_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "auditor@default.com",
        "default_jwt_secret_key_32_chars_minimum_len",
        "HQ-HYD-01",
    )


@pytest.fixture()
def apollo_admin_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "apollo",
        "admin@apollo.com",
        "apollo_jwt_secret_key_32_chars_minimum_len!",
        "APOLLO-HYD-01",
    )


@pytest.fixture()
def sunrise_admin_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "sunrise",
        "admin@sunrise.com",
        "sunrise_jwt_secret_key_32_chars_minimum_len",
        "SUN-BLR-01",
    )