import os
import secrets

# 1. Auto-generate ephemeral test secrets if no .env file exists (e.g. in CI/CD runners)
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("DEVELOPMENT_AUTO_CREATE_SCHEMA", "false")
os.environ.setdefault("API_PORT", "8000")
os.environ.setdefault("API_SERVICE_HOST", "api")
os.environ.setdefault("CONTROL_PLANE_PORT", "8080")
os.environ.setdefault("GUNICORN_WORKERS", "4")
os.environ.setdefault("TENANT_DATABASE_PREFIX", "hms_")
os.environ.setdefault("APP_TIMEZONE", "Asia/Kolkata")
os.environ.setdefault("MAX_PAGE_SIZE", "200")
os.environ.setdefault("MIGRATION_PREVIEW_ROWS", "5")
os.environ.setdefault("MIGRATION_PREVIEW_ERROR_LIMIT", "100")
os.environ.setdefault("MIGRATION_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
os.environ.setdefault("ATTACHMENTS_STORAGE_DIR", "attachments")
os.environ.setdefault("MAX_ATTACHMENT_UPLOAD_BYTES", str(20 * 1024 * 1024))
os.environ.setdefault("MIGRATION_MAX_EXPRESSION_DEPTH", "20")
os.environ.setdefault("MIGRATION_MAX_INPUTS", "50")
os.environ.setdefault("BACKUP_INTERVAL_SECONDS", "86400")
os.environ.setdefault("BACKUP_OUTPUT_DIR", "backups")
os.environ.setdefault("VAULT_POLL_INTERVAL_SECONDS", "30")
os.environ.setdefault("MAIL_PROVIDER", "smtp")
os.environ.setdefault("PLATFORM_NAME", "Avinya HMS")
os.environ.setdefault("BASE_DOMAIN", "localhost")
os.environ.setdefault("DEFAULT_TENANT_SLUG", "default")
os.environ.setdefault("DEFAULT_HOSPITAL_NAME", "Avinya Default Hospital")
os.environ.setdefault("DEFAULT_BRANCH_CODE", "HQ-HYD-01")
os.environ.setdefault("VERSION", "test")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", secrets.token_urlsafe(16))
os.environ.setdefault("POSTGRES_DB", "hms_default")
os.environ.setdefault("POSTGRES_HOST", "db")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("CONTROL_PLANE_DB_NAME", "hms_control_plane")
os.environ.setdefault("REDIS_HOST", "redis")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_DB", "0")
os.environ.setdefault("REDIS_PASSWORD", secrets.token_urlsafe(16))
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault("MASTER_ONBOARDING_KEY", secrets.token_urlsafe(24))
os.environ.setdefault("CONTROL_PLANE_MASTER_KEY", secrets.token_urlsafe(24))
os.environ["CONTROL_PLANE_LEGACY_MASTER_KEY_ENABLED"] = "true"
os.environ.setdefault("BACKUP_ENCRYPTION_SALT", secrets.token_hex(16))
os.environ.setdefault("ENVELOPE_ENCRYPTION_SALT", secrets.token_hex(16))
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
os.environ.setdefault("MFA_REQUIRED_FOR_PRIVILEGED", "false")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@default.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "AdminPassword123!")
os.environ.setdefault("SMTP_PORT", "25")
os.environ.setdefault("SMTP_FROM", "provisioning@example.invalid")

from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.config import settings
from backend.control_plane.models import ControlBase, DeploymentModeEnum, TenantRegistry
from backend.core import database
from backend.core.database import Base, get_control_db, get_redis
from backend.main import app, v1_app
from backend.domains.identity.models import (
    AuthSession,
    Department,
    DoctorProfile,
    GenderEnum,
    Role,
    User,
    UserRole,
    UserStatusEnum,
)
from backend.domains.identity.passwords import pwd_context
from backend.security.envelope import encrypt_tenant_secret
from backend.core import seed
from backend.core.seed import seed_universal_hospital_data
from backend.control_plane.services.catalogs import seed_control_plane

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

    def publish(self, channel: str, message: str) -> int:
        self.store[f"pubsub:{channel}"] = message
        return 1

    def pubsub(self):
        class _DummyPubSub:
            def subscribe(self, *args, **kwargs):
                pass

            def psubscribe(self, *args, **kwargs):
                pass

            def get_message(self, *args, **kwargs):
                return None

            def close(self):
                pass

        return _DummyPubSub()

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
    with TestControlSession() as control_db:
        seed_control_plane(control_db)

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

    # Dynamically generate cryptographically strong keys per test run
    tenant_keys = {
        slug: {
            "jwt": secrets.token_hex(32),
            "backup": secrets.token_urlsafe(32),
        }
        for slug in ("default", "apollo", "sunrise", "suspended", "onprem")
    }

    with TestControlSession() as cdb:
        cdb.add_all([
            TenantRegistry(
                hospital_name="Avinya Default Hospital",
                branch_code="HQ-HYD-01",
                subdomain_slug="default",
                db_name="hms_default",
                admin_email="admin@default.com",
                jwt_secret_key=encrypt_tenant_secret(tenant_keys["default"]["jwt"]),
                backup_encryption_key=encrypt_tenant_secret(tenant_keys["default"]["backup"]),
                deployment_mode=DeploymentModeEnum.CLOUD,
                is_active=True,
            ),
            TenantRegistry(
                hospital_name="Apollo Hospital",
                branch_code="APOLLO-HYD-01",
                subdomain_slug="apollo",
                db_name="hms_apollo",
                admin_email="admin@apollo.com",
                jwt_secret_key=encrypt_tenant_secret(tenant_keys["apollo"]["jwt"]),
                backup_encryption_key=encrypt_tenant_secret(tenant_keys["apollo"]["backup"]),
                deployment_mode=DeploymentModeEnum.CLOUD,
                is_active=True,
            ),
            TenantRegistry(
                hospital_name="Sunrise Hospital",
                branch_code="SUN-BLR-01",
                subdomain_slug="sunrise",
                db_name="hms_sunrise",
                admin_email="admin@sunrise.com",
                jwt_secret_key=encrypt_tenant_secret(tenant_keys["sunrise"]["jwt"]),
                backup_encryption_key=encrypt_tenant_secret(tenant_keys["sunrise"]["backup"]),
                deployment_mode=DeploymentModeEnum.CLOUD,
                is_active=True,
            ),
            TenantRegistry(
                hospital_name="Suspended Hospital",
                branch_code="SUSP-01",
                subdomain_slug="suspended",
                db_name="hms_suspended",
                admin_email="admin@suspended.com",
                jwt_secret_key=encrypt_tenant_secret(tenant_keys["suspended"]["jwt"]),
                backup_encryption_key=encrypt_tenant_secret(tenant_keys["suspended"]["backup"]),
                deployment_mode=DeploymentModeEnum.CLOUD,
                is_active=False,
            ),
            TenantRegistry(
                hospital_name="OnPrem Local Hospital",
                branch_code="ONPREM-01",
                subdomain_slug="onprem",
                db_name="hms_onprem",
                admin_email="admin@onprem.com",
                jwt_secret_key=encrypt_tenant_secret(tenant_keys["onprem"]["jwt"]),
                backup_encryption_key=encrypt_tenant_secret(tenant_keys["onprem"]["backup"]),
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
        "tenant_keys": tenant_keys,
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
        token_id = secrets.token_urlsafe(24)
        token = jwt.encode(
            {
                "sub": str(user.id),
                "tenant_slug": subdomain_slug,
                "branch_code": branch_code,
                "jti": token_id,
                "exp": expire,
            },
            jwt_secret,
            algorithm=settings.ALGORITHM,
        )
        db.add(AuthSession(token_id=token_id, user_id=user.id, expires_at=expire))
        db.commit()
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
        multi_tenant_env["tenant_keys"]["default"]["jwt"],
        "HQ-HYD-01",
    )


@pytest.fixture()
def doctor_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "doctor@default.com",
        multi_tenant_env["tenant_keys"]["default"]["jwt"],
        "HQ-HYD-01",
    )


@pytest.fixture()
def nurse_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "nurse@default.com",
        multi_tenant_env["tenant_keys"]["default"]["jwt"],
        "HQ-HYD-01",
    )


@pytest.fixture()
def pharmacist_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "pharmacist@default.com",
        multi_tenant_env["tenant_keys"]["default"]["jwt"],
        "HQ-HYD-01",
    )


@pytest.fixture()
def billing_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "billing@default.com",
        multi_tenant_env["tenant_keys"]["default"]["jwt"],
        "HQ-HYD-01",
    )


@pytest.fixture()
def auditor_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "default",
        "auditor@default.com",
        multi_tenant_env["tenant_keys"]["default"]["jwt"],
        "HQ-HYD-01",
    )


@pytest.fixture()
def apollo_admin_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "apollo",
        "admin@apollo.com",
        multi_tenant_env["tenant_keys"]["apollo"]["jwt"],
        "APOLLO-HYD-01",
    )


@pytest.fixture()
def sunrise_admin_headers(multi_tenant_env):
    return _mint_test_headers(
        multi_tenant_env,
        "sunrise",
        "admin@sunrise.com",
        multi_tenant_env["tenant_keys"]["sunrise"]["jwt"],
        "SUN-BLR-01",
    )