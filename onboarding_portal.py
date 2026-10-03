from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import io
import logging
import os
from pathlib import Path
import re
import secrets
import smtplib
from typing import Optional
import zipfile


from contextlib import asynccontextmanager
from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from config import settings
from control_models import (
    ControlBase,
    DeploymentModeEnum,
    FederatedNode,
    GlobalPatientPointer,
    TenantRegistry,
)
from database import control_engine, ensure_postgres_db_exists, get_control_db, tenant_engine_pool
from models import (
    Bed,
    BedStatusEnum,
    EncounterStatusEnum,
    FederatedClient,
    Invoice,
    Patient,
    PatientEncounter,
    User,
    UserStatusEnum,
)
from seed import pwd_context, seed_universal_hospital_data
from seed import seed_universal_hospital_data

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.onboarding_portal")

# 1. Define the lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP LOGIC ---
    # This replaces your @onboarding_app.on_event("startup") decorator
    ensure_postgres_db_exists(settings.CONTROL_PLANE_DB_NAME)
    ControlBase.metadata.create_all(bind=control_engine)
    
    yield  # The application runs and handles traffic while frozen right here
    
    # --- SHUTDOWN LOGIC ---
    # If you need to close connections later, put that code here
    pass

# 2. Pass the lifespan handler into the FastAPI initialization block
onboarding_app = FastAPI(
    title="HMS Multi-Tenant Control Plane & Fleet Operations Portal",
    version="3.0.0",
    lifespan=lifespan,
)


def verify_control_plane_secret(
    x_control_plane_secret: Optional[str] = Header(default=None, alias="X-Control-Plane-Secret"),
) -> None:
    """Enforces master key authentication on Control Plane operations when CONTROL_PLANE_MASTER_KEY is set."""
    expected_key = getattr(settings, "CONTROL_PLANE_MASTER_KEY", None)
    if not expected_key:
        return
    if not x_control_plane_secret or not secrets.compare_digest(x_control_plane_secret, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Control-Plane-Secret header for Control Plane provisioning.",
        )


def _verify_vendor_key(provided_key: str) -> None:
    if not provided_key or not secrets.compare_digest(provided_key, settings.MASTER_ONBOARDING_KEY):
        raise HTTPException(status_code=403, detail="Invalid Master Vendor Setup Key.")


class OnboardHospitalRequest(BaseModel):
    master_setup_key: str = Field(min_length=8)
    hospital_name: str = Field(min_length=3, max_length=150)
    subdomain_slug: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9\-]+$")
    branch_code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9\-_]+$")
    admin_email: EmailStr
    deployment_mode: DeploymentModeEnum = DeploymentModeEnum.CLOUD
    seed_icd10: bool = True
    seed_allergies: bool = True
    seed_complaints: bool = True

    @field_validator("subdomain_slug", mode="before")
    @classmethod
    def normalize_subdomain_slug(cls, v: str) -> str:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("branch_code", mode="before")
    @classmethod
    def normalize_branch_code(cls, v: str) -> str:
        return v.strip().upper() if isinstance(v, str) else v


class TenantStatusUpdateRequest(BaseModel):
    master_setup_key: str = Field(min_length=8)
    is_active: bool


class TenantDeploymentModeUpdateRequest(BaseModel):
    master_setup_key: str = Field(min_length=8)
    deployment_mode: DeploymentModeEnum


class TenantKeyRotationRequest(BaseModel):
    master_setup_key: str = Field(min_length=8)
    rotate_jwt_secret: bool = True
    rotate_backup_key: bool = True
    rotate_federation_key: bool = True


class TenantAdminPasswordResetRequest(BaseModel):
    master_setup_key: str = Field(min_length=8)


def _dispatch_welcome_email(to_email: str, data: dict) -> bool:
    if not settings.SMTP_HOST:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = f"HMS Provisioned: {data['hospital_name']} ({data['subdomain_url']})"
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to_email
        msg.set_content(
            f"Welcome to {settings.PLATFORM_NAME}\n\n"
            f"Hospital Name : {data['hospital_name']}\n"
            f"Branch Code   : {data['branch_code']}\n"
            f"Deployment    : {data['deployment_mode']}\n"
            f"Access URL    : {data['subdomain_url']}\n\n"
            f"--- INITIAL CREDENTIALS (CHANGE ON FIRST LOGIN) ---\n"
            f"Admin Email            : {data['admin_email']}\n"
            f"Admin One-Time Password: {data['admin_password']}\n"
            f"Federation API Key     : {data['federation_api_key']}\n"
            f"AES-256 Backup Key     : {data['backup_encryption_key']}\n"
        )
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=5) as server:
            if settings.SMTP_USER:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
            server.send_message(msg)
        return True
    except Exception as exc:
        logger.warning("Email dispatch via docker-mailserver skipped/failed: %s", str(exc))
        return False


def build_onprem_zip_bundle(slug: str, env_content: str) -> bytes:
    onprem_compose = """services:
  db:
    image: postgres:15-alpine
    restart: unless-stopped
    env_file: .env
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER:-user} -d $${POSTGRES_DB}"]
      interval: 3s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redisdata:/data

  api:
    image: avinya-hms-api:latest
    restart: unless-stopped
    env_file: .env
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy

  backup-sidecar:
    image: avinya-hms-api:latest
    restart: unless-stopped
    env_file: .env
    command: ["sh", "-c", "while true; do python backup_service.py; sleep 21600; done"]
    volumes:
      - ./backups:/app/backups
    depends_on:
      db:
        condition: service_healthy

  cloudflared-tunnel:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel --no-autoupdate run --token $${CLOUDFLARE_TUNNEL_TOKEN:-}
    depends_on:
      - api

  auto-updater:
    image: containrrr/watchtower:latest
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    command: --schedule "0 0 3 * * *" --cleanup

volumes:
  pgdata:
  redisdata:
"""
    readme_txt = (
        f"# On-Premise Deployment Bundle ({slug})\n\n"
        f"1. Extract this ZIP on the hospital server.\n"
        f"2. Run: docker compose -f docker-compose.onprem.yml up -d\n"
        f"3. Encrypted catastrophic backups run automatically every 6 hours.\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("docker-compose.onprem.yml", onprem_compose)
        zf.writestr(".env", env_content)
        zf.writestr("README_ONPREM.md", readme_txt)
    buf.seek(0)
    return buf.read()


# =====================================================================
# 1. HOSPITAL PROVISIONING & BUNDLE DOWNLOAD
# =====================================================================

@onboarding_app.post("/api/provision")
def provision_hospital(
    req: OnboardHospitalRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    _verify_vendor_key(req.master_setup_key)

    slug = req.subdomain_slug.strip().lower()
    branch_code = req.branch_code.strip().upper()
    db_slug = re.sub(r"[^a-z0-9_]", "_", slug)
    target_db_name = f"hms_{db_slug}"

    existing = (
        control_db.query(TenantRegistry)
        .filter((TenantRegistry.subdomain_slug == slug) | (TenantRegistry.branch_code == branch_code))
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Subdomain or Branch Code is already registered.")

    jwt_secret = secrets.token_hex(32)
    backup_key = secrets.token_urlsafe(32)
    admin_password = f"Hms!{secrets.token_urlsafe(12)}"
    raw_fed_key = f"fed_live_{secrets.token_urlsafe(32)}"
    fed_key_hash = hashlib.sha256(raw_fed_key.encode("utf-8")).hexdigest()

    if req.deployment_mode == DeploymentModeEnum.CLOUD:
        target_db_url = ensure_postgres_db_exists(target_db_name)
        os.environ["DATABASE_URL"] = target_db_url
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", target_db_url)
        command.upgrade(alembic_cfg, "head")

        t_engine = create_engine(target_db_url)
        TSession = sessionmaker(bind=t_engine, autocommit=False, autoflush=False)
        with TSession() as tenant_db:
            seed_universal_hospital_data(
                db=tenant_db,
                hospital_name=req.hospital_name,
                branch_code=branch_code,
                admin_email=req.admin_email,
                admin_password=admin_password,
                seed_icd10=req.seed_icd10,
                seed_allergies=req.seed_allergies,
                seed_complaints=req.seed_complaints,
            )
            tenant_db.add(
                FederatedClient(
                    branch_code=branch_code,
                    name=req.hospital_name,
                    api_key_prefix=raw_fed_key[:14],
                    api_key_hash=fed_key_hash,
                    is_active=True,
                )
            )
            tenant_db.commit()
        t_engine.dispose()

    subdomain_url = f"https://{slug}.{settings.BASE_DOMAIN}"
    tenant_entry = TenantRegistry(
        hospital_name=req.hospital_name,
        branch_code=branch_code,
        subdomain_slug=slug,
        db_name=target_db_name,
        admin_email=req.admin_email,
        jwt_secret_key=jwt_secret,
        backup_encryption_key=backup_key,
        deployment_mode=req.deployment_mode,
        api_base_url=subdomain_url,
        is_active=True,
    )
    fed_node = FederatedNode(
        branch_code=branch_code,
        hospital_name=req.hospital_name,
        subdomain_slug=slug,
        api_key_prefix=raw_fed_key[:14],
        api_key_hash=fed_key_hash,
        is_active=True,
    )
    control_db.add_all([tenant_entry, fed_node])
    control_db.commit()

    out_dir = Path("deployments")
    out_dir.mkdir(exist_ok=True)
    env_content = (
        f"# HMS Configuration Bundle - {req.hospital_name} ({branch_code})\n"
        f"# Mode: {req.deployment_mode.value} | Generated: {datetime.now(timezone.utc).isoformat()}\n"
        f'HOSPITAL_NAME="{req.hospital_name}"\n'
        f'HOSPITAL_BRANCH_CODE="{branch_code}"\n'
        f'DEFAULT_TENANT_SLUG="{slug}"\n'
        f"POSTGRES_DB={target_db_name}\n"
        f"SECRET_KEY={jwt_secret}\n"
        f"FEDERATION_API_KEY={raw_fed_key}\n"
        f"BACKUP_ENCRYPTION_KEY={backup_key}\n"
        f"SEED_ADMIN_EMAIL={req.admin_email}\n"
        f"SEED_ADMIN_PASSWORD={admin_password}\n"
    )
    env_path = out_dir / f"{slug}.env"
    env_path.write_text(env_content, encoding="utf-8")

    result = {
        "hospital_name": req.hospital_name,
        "branch_code": branch_code,
        "subdomain_slug": slug,
        "subdomain_url": subdomain_url,
        "deployment_mode": req.deployment_mode.value,
        "database_name": target_db_name,
        "admin_email": req.admin_email,
        "admin_password": admin_password,
        "jwt_secret_key": jwt_secret,
        "federation_api_key": raw_fed_key,
        "backup_encryption_key": backup_key,
        "env_content": env_content,
        "zip_bundle_url": f"/api/deployments/{slug}/bundle.zip",
    }
    result["email_dispatched"] = _dispatch_welcome_email(req.admin_email, result)
    return result


@onboarding_app.get("/api/deployments/{slug}/bundle.zip")
def download_onprem_bundle(slug: str):
    env_path = Path("deployments") / f"{slug.lower()}.env"
    if not env_path.exists():
        raise HTTPException(status_code=404, detail="Deployment bundle not found.")
    zip_bytes = build_onprem_zip_bundle(slug.lower(), env_path.read_text(encoding="utf-8"))
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug.lower()}-onprem-bundle.zip"'},
    )


# =====================================================================
# 2. DAY-2 FLEET MANAGEMENT, CIRCUIT-BREAKER, KEY ROTATION & TELEMETRY
# =====================================================================

@onboarding_app.get("/api/tenants")
def list_registered_tenants(
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Lists all registered hospitals in the Control Plane with federation and pointer counts."""
    _verify_vendor_key(x_master_setup_key or "")
    tenants = list(control_db.execute(select(TenantRegistry).order_by(TenantRegistry.id.asc())).scalars().all())
    fed_nodes = {
        f.branch_code: f
        for f in control_db.execute(select(FederatedNode)).scalars().all()
    }
    pointer_counts = dict(
        control_db.execute(
            select(GlobalPatientPointer.branch_code, func.count(GlobalPatientPointer.id)).group_by(
                GlobalPatientPointer.branch_code
            )
        ).all()
    )

    items = []
    for t in tenants:
        fn = fed_nodes.get(t.branch_code)
        items.append(
            {
                "id": t.id,
                "hospital_name": t.hospital_name,
                "branch_code": t.branch_code,
                "subdomain_slug": t.subdomain_slug,
                "db_name": t.db_name,
                "admin_email": t.admin_email,
                "deployment_mode": t.deployment_mode.value,
                "is_active": t.is_active,
                "federation_active": fn.is_active if fn else False,
                "federation_key_prefix": fn.api_key_prefix if fn else None,
                "global_patient_pointers_count": pointer_counts.get(t.branch_code, 0),
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
        )
    return {"total": len(items), "items": items}


@onboarding_app.patch("/api/tenants/{slug}/status")
def toggle_tenant_circuit_breaker(
    slug: str,
    payload: TenantStatusUpdateRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Suspends or reactivates a hospital tenant and its federation node immediately."""
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    tenant.is_active = payload.is_active
    fed_node = control_db.execute(
        select(FederatedNode).where(FederatedNode.branch_code == tenant.branch_code)
    ).scalar_one_or_none()
    if fed_node:
        fed_node.is_active = payload.is_active

    control_db.commit()
    return {
        "status": "success",
        "subdomain_slug": tenant.subdomain_slug,
        "is_active": tenant.is_active,
        "message": f"Tenant '{tenant.subdomain_slug}' is now {'ACTIVE' if tenant.is_active else 'SUSPENDED'}.",
    }


@onboarding_app.patch("/api/tenants/{slug}/deployment-mode")
def switch_tenant_deployment_mode(
    slug: str,
    payload: TenantDeploymentModeUpdateRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Switches a tenant between CLOUD and ON_PREM modes (e.g. for emergency cloud failover)."""
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    tenant.deployment_mode = payload.deployment_mode
    control_db.commit()
    return {
        "status": "success",
        "subdomain_slug": tenant.subdomain_slug,
        "deployment_mode": tenant.deployment_mode.value,
    }


@onboarding_app.post("/api/tenants/{slug}/rotate-keys")
def rotate_tenant_cryptographic_keys(
    slug: str,
    payload: TenantKeyRotationRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Rotates JWT secret, AES-256 backup encryption key, and/or Federation API key for a tenant."""
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    rotated = {"subdomain_slug": tenant.subdomain_slug}
    if payload.rotate_jwt_secret:
        tenant.jwt_secret_key = secrets.token_hex(32)
        rotated["new_jwt_secret_key"] = tenant.jwt_secret_key

    if payload.rotate_backup_key:
        tenant.backup_encryption_key = secrets.token_urlsafe(32)
        rotated["new_backup_encryption_key"] = tenant.backup_encryption_key

    if payload.rotate_federation_key:
        raw_fed_key = f"fed_live_{secrets.token_urlsafe(32)}"
        fed_hash = hashlib.sha256(raw_fed_key.encode("utf-8")).hexdigest()
        fed_node = control_db.execute(
            select(FederatedNode).where(FederatedNode.branch_code == tenant.branch_code)
        ).scalar_one_or_none()
        if fed_node:
            fed_node.api_key_prefix = raw_fed_key[:14]
            fed_node.api_key_hash = fed_hash
        rotated["new_federation_api_key"] = raw_fed_key

        if tenant.deployment_mode == DeploymentModeEnum.CLOUD:
            try:
                t_engine = tenant_engine_pool.get_engine(tenant.db_name)
                TSession = sessionmaker(bind=t_engine, autocommit=False, autoflush=False)
                with TSession() as t_db:
                    fc = t_db.execute(
                        select(FederatedClient).where(FederatedClient.branch_code == tenant.branch_code)
                    ).scalar_one_or_none()
                    if fc:
                        fc.api_key_prefix = raw_fed_key[:14]
                        fc.api_key_hash = fed_hash
                        t_db.commit()
            except Exception as exc:
                logger.warning("Could not sync rotated federation key to tenant DB: %s", str(exc))

    control_db.commit()
    return rotated



@onboarding_app.post("/api/tenants/{slug}/reset-admin-password")
def emergency_reset_tenant_admin_password(
    slug: str,
    payload: TenantAdminPasswordResetRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Unlocks the tenant's primary superuser account and generates a fresh one-time password."""
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    new_password = f"HmsReset!{secrets.token_urlsafe(10)}"
    t_engine = tenant_engine_pool.get_engine(tenant.db_name)
    TSession = sessionmaker(bind=t_engine, autocommit=False, autoflush=False)
    with TSession() as t_db:
        admin_user = t_db.execute(
            select(User)
            .where((User.email == tenant.admin_email) | (User.is_superuser == True))
            .order_by(User.id.asc())
        ).scalars().first()
        if not admin_user:
            raise HTTPException(status_code=404, detail="Superuser account not found in tenant database.")

        admin_user.hashed_password = pwd_context.hash(new_password)
        admin_user.status = UserStatusEnum.ACTIVE
        admin_user.failed_login_attempts = 0
        admin_user.password_changed_at = None
        t_db.commit()
        admin_email = admin_user.email

    return {
        "status": "success",
        "subdomain_slug": tenant.subdomain_slug,
        "admin_email": admin_email,
        "one_time_password": new_password,
    }


@onboarding_app.get("/api/tenants/{slug}/telemetry")
def get_tenant_operational_telemetry(
    slug: str,
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Returns live non-PHI operational telemetry for a specific tenant database."""
    _verify_vendor_key(x_master_setup_key or "")
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    t_engine = tenant_engine_pool.get_engine(tenant.db_name)
    TSession = sessionmaker(bind=t_engine, autocommit=False, autoflush=False)
    with TSession() as t_db:
        users_count = t_db.scalar(select(func.count(User.id))) or 0
        patients_count = t_db.scalar(select(func.count(Patient.id))) or 0
        active_encounters = (
            t_db.scalar(
                select(func.count(PatientEncounter.id)).where(
                    PatientEncounter.status == EncounterStatusEnum.ACTIVE
                )
            )
            or 0
        )
        total_beds = t_db.scalar(select(func.count(Bed.id)).where(Bed.is_active == True)) if hasattr(Bed, "is_active") else (t_db.scalar(select(func.count(Bed.id))) or 0)
        occupied_beds = (
            t_db.scalar(select(func.count(Bed.id)).where(Bed.status == BedStatusEnum.OCCUPIED)) or 0
        )
        invoices_count = t_db.scalar(select(func.count(Invoice.id))) or 0
        gross_revenue_paise = t_db.scalar(select(func.coalesce(func.sum(Invoice.total_amount_paise), 0))) or 0
        collected_revenue_paise = t_db.scalar(select(func.coalesce(func.sum(Invoice.amount_paid_paise), 0))) or 0

    backups_dir = Path("backups")
    backups = sorted(backups_dir.glob(f"{tenant.db_name}_*.dump.enc")) if backups_dir.exists() else []

    return {
        "subdomain_slug": tenant.subdomain_slug,
        "hospital_name": tenant.hospital_name,
        "branch_code": tenant.branch_code,
        "db_name": tenant.db_name,
        "deployment_mode": tenant.deployment_mode.value,
        "is_active": tenant.is_active,
        "metrics": {
            "users_count": users_count,
            "patients_count": patients_count,
            "active_encounters_count": active_encounters,
            "total_beds_count": total_beds,
            "occupied_beds_count": occupied_beds,
            "invoices_count": invoices_count,
            "gross_revenue_paise": gross_revenue_paise,
            "collected_revenue_paise": collected_revenue_paise,
            "encrypted_backups_count": len(backups),
            "latest_backup_file": backups[-1].name if backups else None,
        },
    }

@onboarding_app.post("/api/tenants/sync-fleet")
def sync_all_cloud_tenants_schema_and_templates(
    payload: TenantAdminPasswordResetRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Idempotently syncs universal dictionaries, RBAC permissions, and all 8 standard report templates across all active CLOUD tenants."""
    _verify_vendor_key(payload.master_setup_key)
    cloud_tenants = list(
        control_db.execute(
            select(TenantRegistry).where(
                TenantRegistry.is_active == True,
                TenantRegistry.deployment_mode == DeploymentModeEnum.CLOUD,
            )
        )
        .scalars()
        .all()
    )

    synced_slugs = []
    for t in cloud_tenants:
        t_engine = tenant_engine_pool.get_engine(t.db_name)
        TSession = sessionmaker(bind=t_engine, autocommit=False, autoflush=False)
        with TSession() as t_db:
            seed_universal_hospital_data(
                db=t_db,
                hospital_name=t.hospital_name,
                branch_code=t.branch_code,
                admin_email=t.admin_email,
                admin_password="KeepExistingPassword123!",
            )
            t_db.commit()
        synced_slugs.append(t.subdomain_slug)

    return {
        "status": "success",
        "synced_tenants_count": len(synced_slugs),
        "synced_subdomains": synced_slugs,
    }

@onboarding_app.get("/", response_class=HTMLResponse)
def render_portal():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Avinya HMS v3.0 — Multi-Tenant Control Plane & Fleet Operations</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen p-6">
  <div class="max-w-6xl mx-auto bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl p-8">
    <div class="flex flex-wrap items-center justify-between border-b border-slate-800 pb-4 mb-6 gap-4">
      <div>
        <h1 class="text-2xl font-bold text-white">Avinya HMS — Enterprise Multi-Tenant Control Plane</h1>
        <p class="text-sm text-slate-400 mt-1">Zero-Touch Hospital Provisioning, Live Fleet Telemetry, Circuit-Breaker & Cryptographic Key Rotation</p>
      </div>
      <div class="flex space-x-2">
        <button onclick="switchTab('provision')" id="tabProvisionBtn" class="px-4 py-2 text-xs font-semibold rounded-lg bg-indigo-600 text-white">1. Provision New Hospital</button>
        <button onclick="switchTab('fleet')" id="tabFleetBtn" class="px-4 py-2 text-xs font-semibold rounded-lg bg-slate-800 text-slate-300 hover:bg-slate-700">2. Fleet Registry & Operations</button>
      </div>
    </div>

    <!-- TAB 1: PROVISIONING -->
    <div id="tabProvision">
      <form id="onboardForm" class="space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Master Vendor Key</label>
            <input type="password" id="master_setup_key" required placeholder="Vendor authorization key"
              class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-sm" />
          </div>
          <div>
            <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Deployment Target</label>
            <select id="deployment_mode" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-sm">
              <option value="CLOUD">Cloud SaaS (Instant Subdomain + Shared API Pool)</option>
              <option value="ON_PREM">On-Premise Hospital Server (Generate ZIP Bundle + Cloud Backup Key)</option>
            </select>
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Hospital Name</label>
            <input type="text" id="hospital_name" required placeholder="Sunrise Hospital"
              class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-sm" />
          </div>
          <div>
            <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Subdomain Slug</label>
            <input type="text" id="subdomain_slug" required placeholder="sunrise"
              class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-sm lowercase" />
          </div>
          <div>
            <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Branch Code</label>
            <input type="text" id="branch_code" required placeholder="SUN-HYD-01"
              class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-sm uppercase" />
          </div>
        </div>

        <div>
          <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Hospital Admin Email (Receives Credentials)</label>
          <input type="email" id="admin_email" required placeholder="it-admin@sunrisehospital.com"
            class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-sm" />
        </div>

        <div class="bg-slate-950 border border-slate-800 rounded-xl p-4">
          <p class="text-xs font-semibold uppercase text-slate-400 mb-3">Universal Clinical Dictionaries & 8 Print Templates</p>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
            <label class="flex items-center space-x-2"><input type="checkbox" id="seed_icd10" checked /><span>ICD-10 Diagnoses</span></label>
            <label class="flex items-center space-x-2"><input type="checkbox" id="seed_allergies" checked /><span>Standard Allergies</span></label>
            <label class="flex items-center space-x-2"><input type="checkbox" id="seed_complaints" checked /><span>Chief Complaints</span></label>
          </div>
        </div>

        <div id="errorBox" class="hidden bg-rose-500/10 border border-rose-500/30 text-rose-300 text-sm rounded-lg p-3"></div>

        <button type="submit" id="submitBtn" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-semibold py-3 rounded-xl shadow-lg transition">
          Deploy Hospital & Generate Security Keys
        </button>
      </form>
    </div>

    <!-- TAB 2: FLEET REGISTRY & DAY-2 OPERATIONS -->
    <div id="tabFleet" class="hidden space-y-4">
      <div class="flex items-center space-x-3">
        <input type="password" id="fleet_master_key" placeholder="Enter Master Vendor Key to load Fleet Registry"
          class="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-4 py-2 text-sm" />
        <button onclick="loadFleet()" class="bg-emerald-600 hover:bg-emerald-500 text-white px-5 py-2 rounded-lg text-sm font-semibold">Load Fleet</button>
      </div>
      <div id="fleetTableContainer" class="overflow-x-auto bg-slate-950 border border-slate-800 rounded-xl p-4 text-sm text-slate-400">
        Enter Master Vendor Key above and click "Load Fleet" to inspect or manage active hospital tenants.
      </div>
    </div>
  </div>

  <div id="resultModal" class="hidden fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 z-50">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-2xl w-full p-6 space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h2 class="text-xl font-bold text-emerald-400">Control Plane Action Result</h2>
        <button onclick="document.getElementById('resultModal').classList.add('hidden')" class="text-slate-400 hover:text-white">✕</button>
      </div>
      <div id="modalBody" class="space-y-2 text-xs font-mono bg-slate-950 p-4 rounded-xl border border-slate-800 overflow-x-auto"></div>
      <div class="flex justify-end space-x-3">
        <a id="downloadZipBtn" href="#" class="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg text-sm">Download On-Premise ZIP</a>
        <button id="downloadEnvBtn" class="bg-slate-800 hover:bg-slate-700 text-white px-4 py-2 rounded-lg text-sm">Download .env</button>
        <button id="copyBtn" class="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg text-sm">Copy JSON</button>
      </div>
    </div>
  </div>

  <script>
    let latestData = null;
    function switchTab(tab) {
      document.getElementById("tabProvision").classList.toggle("hidden", tab !== "provision");
      document.getElementById("tabFleet").classList.toggle("hidden", tab !== "fleet");
      document.getElementById("tabProvisionBtn").className = tab === "provision" ? "px-4 py-2 text-xs font-semibold rounded-lg bg-indigo-600 text-white" : "px-4 py-2 text-xs font-semibold rounded-lg bg-slate-800 text-slate-300";
      document.getElementById("tabFleetBtn").className = tab === "fleet" ? "px-4 py-2 text-xs font-semibold rounded-lg bg-indigo-600 text-white" : "px-4 py-2 text-xs font-semibold rounded-lg bg-slate-800 text-slate-300";
    }

    async function loadFleet() {
      const key = document.getElementById("fleet_master_key").value.trim();
      const box = document.getElementById("fleetTableContainer");
      const res = await fetch("/api/tenants", { headers: { "X-Master-Setup-Key": key } });
      const data = await res.json();
      if (!res.ok) { box.innerHTML = `<span class="text-rose-400">${data.detail || 'Error loading fleet'}</span>`; return; }
      let rows = data.items.map(t => `
        <tr class="border-b border-slate-800">
          <td class="py-2 px-3 font-bold text-white">${t.hospital_name} <span class="text-xs text-slate-400">(${t.subdomain_slug})</span></td>
          <td class="py-2 px-3">${t.branch_code}</td>
          <td class="py-2 px-3">${t.deployment_mode}</td>
          <td class="py-2 px-3">${t.is_active ? '<span class="text-emerald-400">ACTIVE</span>' : '<span class="text-rose-400">SUSPENDED</span>'}</td>
          <td class="py-2 px-3">${t.global_patient_pointers_count}</td>
        </tr>`).join("");
      box.innerHTML = `<table class="w-full text-left text-xs"><thead><tr class="text-slate-400 border-b border-slate-800"><th class="py-2 px-3">Hospital</th><th class="py-2 px-3">Branch</th><th class="py-2 px-3">Mode</th><th class="py-2 px-3">Status</th><th class="py-2 px-3">MPI Pointers</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    document.getElementById("onboardForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = document.getElementById("submitBtn");
      const errBox = document.getElementById("errorBox");
      errBox.classList.add("hidden");
      btn.disabled = true;
      btn.textContent = "Provisioning Isolated Database, RBAC Matrix & Keys...";
      try {
        const res = await fetch("/api/provision", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            master_setup_key: document.getElementById("master_setup_key").value.trim(),
            deployment_mode: document.getElementById("deployment_mode").value,
            hospital_name: document.getElementById("hospital_name").value.trim(),
            subdomain_slug: document.getElementById("subdomain_slug").value.trim().toLowerCase(),
            branch_code: document.getElementById("branch_code").value.trim().toUpperCase(),
            admin_email: document.getElementById("admin_email").value.trim().toLowerCase(),
            seed_icd10: document.getElementById("seed_icd10").checked,
            seed_allergies: document.getElementById("seed_allergies").checked,
            seed_complaints: document.getElementById("seed_complaints").checked,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          const errMsg = Array.isArray(data.detail)
            ? data.detail.map(d => `${d.loc.slice(-1)[0]}: ${d.msg}`).join(" | ")
            : (data.detail || "Deployment failed");
          throw new Error(errMsg);
        }
        latestData = data;
        document.getElementById("downloadZipBtn").href = data.zip_bundle_url;
        document.getElementById("modalBody").innerHTML = `
          <div><span class="text-slate-400">Subdomain URL:</span> <span class="text-emerald-400 font-bold">${data.subdomain_url}</span></div>
          <div><span class="text-slate-400">Tenant Header (Local Dev):</span> X-Hospital-Subdomain: ${data.subdomain_slug}</div>
          <div><span class="text-slate-400">Isolated Database:</span> ${data.database_name} (${data.deployment_mode})</div>
          <div><span class="text-slate-400">Admin Login:</span> ${data.admin_email}</div>
          <div><span class="text-slate-400">One-Time Admin Password:</span> <span class="text-amber-300 font-bold">${data.admin_password}</span></div>
          <div><span class="text-slate-400">Federation Key (X-Federation-API-Key):</span> <span class="text-indigo-300">${data.federation_api_key}</span></div>
          <div><span class="text-slate-400">Catastrophic AES-256 Backup Key:</span> <span class="text-rose-300">${data.backup_encryption_key}</span></div>
          <div><span class="text-slate-400">Email Status:</span> ${data.email_dispatched ? "Sent via docker-mailserver" : "Displayed on screen only (SMTP offline)"}</div>
        `;
        document.getElementById("resultModal").classList.remove("hidden");
      } catch (err) {
        errBox.textContent = err.message;
        errBox.classList.remove("hidden");
      } finally {
        btn.disabled = false;
        btn.textContent = "Deploy Hospital & Generate Security Keys";
      }
    });
    document.getElementById("copyBtn").addEventListener("click", () => {
      if (latestData) navigator.clipboard.writeText(JSON.stringify(latestData, null, 2));
    });
    document.getElementById("downloadEnvBtn").addEventListener("click", () => {
      if (!latestData) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([latestData.env_content], { type: "text/plain" }));
      a.download = `${latestData.subdomain_slug}.env`;
      a.click();
    });
  </script>
</body>
</html>"""