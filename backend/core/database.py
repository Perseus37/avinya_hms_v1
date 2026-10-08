from collections import OrderedDict
from dataclasses import dataclass
import threading
from typing import Optional
from fastapi import HTTPException, Request, status
import redis
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings
from control_models import DeploymentModeEnum, TenantRegistry
from security.envelope import decrypt_tenant_secret

MAX_CACHED_TENANT_ENGINES = 64


class Base(DeclarativeBase):
    """Declarative base for all hospital tenant database models."""
    pass


@dataclass(frozen=True)
class TenantContext:
    subdomain_slug: str
    branch_code: str
    hospital_name: str
    db_name: str
    jwt_secret_key: str
    enabled_modules_json: Optional[str] = '{"all": true}'
    quota_limits_json: Optional[str] = None


def build_db_url(db_name: str) -> str:
    """Constructs an unmasked PostgreSQL connection URL for a specific database name."""
    base_url = make_url(settings.DATABASE_URL)
    if base_url.drivername in ("postgresql", "postgres"):
        base_url = base_url.set(drivername="postgresql+psycopg2")
    return base_url.set(database=db_name).render_as_string(hide_password=False)


def ensure_postgres_db_exists(db_name: str) -> str:
    """Idempotently creates a PostgreSQL database if it does not already exist."""
    admin_url = build_db_url("postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": db_name}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        admin_engine.dispose()
    return build_db_url(db_name)


# Control Plane Engine (hms_control_plane)
control_engine = create_engine(
    build_db_url(settings.CONTROL_PLANE_DB_NAME),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
ControlSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=control_engine)


class TenantEngineRegistry:
    """Thread-safe LRU cache of SQLAlchemy Engines per tenant database."""
    def __init__(self, max_engines: int = MAX_CACHED_TENANT_ENGINES):
        self._engines: OrderedDict[str, Engine] = OrderedDict()
        self._lock = threading.Lock()
        self._max_engines = max_engines

    def get_engine(self, db_name: str) -> Engine:
        with self._lock:
            if db_name in self._engines:
                self._engines.move_to_end(db_name)
                return self._engines[db_name]

            if len(self._engines) >= self._max_engines:
                _, oldest_engine = self._engines.popitem(last=False)
                oldest_engine.dispose()

            from sqlalchemy.pool import NullPool
            
            # ARCH-001: Prevent connection pool exhaustion across 4x Gunicorn workers
            tenant_engine = create_engine(
                build_db_url(db_name),
                poolclass=NullPool,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
            self._engines[db_name] = tenant_engine
            return tenant_engine


tenant_engine_pool = TenantEngineRegistry()
redis_pool = redis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)


def extract_subdomain_slug(request: Request) -> str:
    """Extracts the tenant slug from X-Hospital-Subdomain header or Host subdomain."""
    # SEC-002: Restrict X-Hospital-Subdomain header extraction to internal networks
    # Prevents external attackers from spoofing tenant routing
    header_override = request.headers.get("X-Hospital-Subdomain")
    is_test_env = getattr(settings, "ENVIRONMENT", "") in ("development", "testing")
    
    if header_override and (getattr(settings, "ALLOW_HEADER_TENANT_OVERRIDE", False) or is_test_env):
        return header_override.strip().lower()

    host = request.headers.get("host", "").split(":")[0].strip().lower()
    if host and host != settings.BASE_DOMAIN and host.endswith(f".{settings.BASE_DOMAIN}"):
        slug = host[: -(len(settings.BASE_DOMAIN) + 1)]
        if slug and "." not in slug:
            return slug

    return settings.DEFAULT_TENANT_SLUG


def resolve_tenant_context(request: Request) -> TenantContext:
    """Looks up the active tenant in hms_control_plane and caches it on request.state."""
    if hasattr(request.state, "tenant_context") and request.state.tenant_context:
        return request.state.tenant_context

    slug = extract_subdomain_slug(request)
    with ControlSessionLocal() as control_db:
        stmt = select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug)
        tenant = control_db.execute(stmt).scalar_one_or_none()

        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Hospital subdomain '{slug}' is not registered.",
            )
        if not tenant.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Hospital instance '{tenant.hospital_name}' is currently suspended.",
            )
        if tenant.deployment_mode == DeploymentModeEnum.ON_PREM and slug != settings.DEFAULT_TENANT_SLUG:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This hospital runs On-Premise. Route requests to its local server endpoint.",
            )
        if getattr(tenant, "maintenance_mode", False) and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=tenant.maintenance_notice
                or "Hospital is in a scheduled read-only maintenance window. Viewing patient charts is enabled; writes are temporarily paused.",
            )
        ctx = TenantContext(
            subdomain_slug=tenant.subdomain_slug,
            branch_code=tenant.branch_code,
            hospital_name=tenant.hospital_name,
            db_name=tenant.db_name,
            jwt_secret_key=decrypt_tenant_secret(tenant.jwt_secret_key),
            enabled_modules_json=getattr(tenant, "enabled_modules_json", '{"all": true}'),
            quota_limits_json=getattr(tenant, "quota_limits_json", None),
        )
        request.state.tenant_context = ctx
        return ctx


def get_control_db():
    """Yields a session connected to the central hms_control_plane database."""
    db = ControlSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db(request: Request):
    """Dynamically routes the SQLAlchemy session to the requesting hospital's isolated database."""
    ctx = resolve_tenant_context(request)
    engine = tenant_engine_pool.get_engine(ctx.db_name)
    TenantSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db: Session = TenantSession()
    try:
        yield db
    finally:
        db.close()


def get_redis():
    return redis.Redis(connection_pool=redis_pool)