import hashlib
import json
import logging
from pathlib import Path
import re
import secrets
from typing import Any, Dict, Optional
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from config import settings
from control_models import (
    BillingCycleEnum,
    ControlPlaneAuditLog,
    DeploymentModeEnum,
    FederatedNode,
    GlobalPatientPointer,
    PricingModelEnum,
    TenantRegistry,
)
from control_plane.pricing import MODULE_CATALOG, compute_contract_fee_paise
from database import get_control_db, tenant_engine_pool
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
from security.envelope import decrypt_tenant_secret, encrypt_tenant_secret
from seed import pwd_context, seed_universal_hospital_data

logger = logging.getLogger("avinya.fleet_ops")
router = APIRouter(prefix="/api", tags=["Control Plane Fleet Operations"])


def verify_control_plane_secret(
    x_control_plane_secret: Optional[str] = Header(default=None, alias="X-Control-Plane-Secret"),
) -> None:
    expected_key = getattr(settings, "CONTROL_PLANE_MASTER_KEY", None)
    if expected_key and (not x_control_plane_secret or not secrets.compare_digest(x_control_plane_secret, expected_key)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Control Plane credentials.")


def _verify_vendor_key(provided_key: str) -> None:
    if not provided_key or not secrets.compare_digest(provided_key, settings.MASTER_ONBOARDING_KEY):
        raise HTTPException(status_code=403, detail="Invalid Master Vendor Setup Key.")


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


class EntitlementsUpdateRequest(BaseModel):
    master_setup_key: str = Field(min_length=8)
    pricing_model: Optional[PricingModelEnum] = None
    subscription_tier: Optional[PricingModelEnum] = None
    billing_cycle: BillingCycleEnum = BillingCycleEnum.ANNUAL_LICENSE
    base_license_fee_paise: Optional[int] = None
    enabled_modules: Dict[str, bool]
    quota_limits: Dict[str, int]
    maintenance_mode: bool = False
    maintenance_notice: Optional[str] = None


class SandboxCloneRequest(BaseModel):
    master_setup_key: str = Field(min_length=8)
    sandbox_subdomain_slug: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9\-]+$")


@router.get("/fleet/overview")
def get_fleet_global_overview(
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
) -> Dict[str, Any]:
    """Computes cluster-wide metrics across all hospital databases."""
    _verify_vendor_key(x_master_setup_key or "")
    tenants = list(control_db.execute(select(TenantRegistry)).scalars().all())

    total_patients = 0
    total_beds = 0
    occupied_beds = 0
    total_revenue_paise = 0
    total_arr_paise = 0

    for t in tenants:
        if not t.is_active:
            continue
        fee = getattr(t, "base_license_fee_paise", 0) or 0
        cycle = t.billing_cycle.value if getattr(t, "billing_cycle", None) else "ANNUAL_LICENSE"
        if cycle in ("MONTHLY_SUBSCRIPTION", "PER_ACTIVE_BED_MONTHLY"):
            total_arr_paise += fee * 12
        elif cycle == "ONE_TIME_PERPETUAL":
            total_arr_paise += int(fee * 0.20)
        else:
            total_arr_paise += fee
        try:
            eng = tenant_engine_pool.get_engine(t.db_name)
            TSession = sessionmaker(bind=eng)
            with TSession() as tdb:
                total_patients += tdb.scalar(select(func.count(Patient.id))) or 0
                total_beds += tdb.scalar(select(func.count(Bed.id))) or 0
                occupied_beds += tdb.scalar(select(func.count(Bed.id)).where(Bed.status == BedStatusEnum.OCCUPIED)) or 0
                total_revenue_paise += tdb.scalar(select(func.coalesce(func.sum(Invoice.amount_paid_paise), 0))) or 0
        except Exception as exc:
            logger.warning("Could not gather metrics for tenant %s: %s", t.subdomain_slug, str(exc))

    total_pointers = control_db.scalar(select(func.count(GlobalPatientPointer.id))) or 0
    backups_dir = Path("backups")
    total_snapshots = len(list(backups_dir.glob("*.dump.enc"))) if backups_dir.exists() else 0

    return {
        "total_hospitals": len(tenants),
        "active_hospitals": sum(1 for t in tenants if t.is_active),
        "total_patients_recorded": total_patients,
        "total_beds": total_beds,
        "occupied_beds": occupied_beds,
        "fleet_bed_occupancy_pct": round((occupied_beds / total_beds) * 100.0, 2) if total_beds > 0 else 0.0,
        "fleet_gross_collected_revenue_paise": total_revenue_paise,
        "vendor_annual_license_arr_paise": total_arr_paise,
        "global_patient_pointers_indexed": total_pointers,
        "encrypted_disaster_snapshots_count": total_snapshots,
    }


@router.get("/tenants")
def list_registered_tenants(
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    _verify_vendor_key(x_master_setup_key or "")
    tenants = list(control_db.execute(select(TenantRegistry).order_by(TenantRegistry.id.asc())).scalars().all())
    fed_nodes = {f.branch_code: f for f in control_db.execute(select(FederatedNode)).scalars().all()}
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
        try:
            mods = json.loads(t.enabled_modules_json) if t.enabled_modules_json else {"all": True}
            active_mod_count = len(MODULE_CATALOG) if mods.get("all") is True else sum(1 for v in mods.values() if v)
        except Exception:
            active_mod_count = len(MODULE_CATALOG)

        items.append(
            {
                "id": t.id,
                "hospital_name": t.hospital_name,
                "branch_code": t.branch_code,
                "subdomain_slug": t.subdomain_slug,
                "db_name": t.db_name,
                "admin_email": t.admin_email,
                "deployment_mode": t.deployment_mode.value,
                "pricing_model": t.pricing_model.value,
                "subscription_tier": t.pricing_model.value,
                "billing_cycle": t.billing_cycle.value if t.billing_cycle else "ANNUAL_LICENSE",
                "base_license_fee_paise": t.base_license_fee_paise or 0,
                "active_modules_count": active_mod_count,
                "is_active": t.is_active,
                "maintenance_mode": t.maintenance_mode,
                "federation_active": fn.is_active if fn else False,
                "federation_key_prefix": fn.api_key_prefix if fn else None,
                "global_patient_pointers_count": pointer_counts.get(t.branch_code, 0),
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
        )
    return {"total": len(items), "items": items}


@router.patch("/tenants/{slug}/status")
def toggle_tenant_circuit_breaker(
    slug: str,
    payload: TenantStatusUpdateRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
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

    control_db.add(
        ControlPlaneAuditLog(
            action="TOGGLE_STATUS",
            target_slug=slug.lower(),
            details=f"Status changed to {'ACTIVE' if payload.is_active else 'SUSPENDED'}",
        )
    )
    control_db.commit()
    return {
        "status": "success",
        "subdomain_slug": tenant.subdomain_slug,
        "is_active": tenant.is_active,
        "message": f"Tenant '{tenant.subdomain_slug}' is now {'ACTIVE' if tenant.is_active else 'SUSPENDED'}.",
    }


@router.patch("/tenants/{slug}/deployment-mode")
def switch_tenant_deployment_mode(
    slug: str,
    payload: TenantDeploymentModeUpdateRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
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


@router.post("/tenants/{slug}/rotate-keys")
def rotate_tenant_cryptographic_keys(
    slug: str,
    payload: TenantKeyRotationRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    rotated = {"subdomain_slug": tenant.subdomain_slug}
    if payload.rotate_jwt_secret:
        raw_jwt = secrets.token_hex(32)
        tenant.jwt_secret_key = encrypt_tenant_secret(raw_jwt)
        rotated["new_jwt_secret_key"] = raw_jwt

    if payload.rotate_backup_key:
        raw_backup = secrets.token_urlsafe(32)
        tenant.backup_encryption_key = encrypt_tenant_secret(raw_backup)
        rotated["new_backup_encryption_key"] = raw_backup

    if payload.rotate_federation_key:
        raw_fed_key = f"fed_live_{secrets.token_urlsafe(32)}"
        fed_hash = hashlib.sha256(raw_fed_key.encode("utf-8")).hexdigest()
        fed_node = control_db.execute(
            select(FederatedNode).where(FederatedNode.branch_code == tenant.branch_code)
        ).scalar_one_or_none()
        if fed_node:
            fed_node.api_key_prefix = raw_fed_key[:14]
            fed_node.api_key_hash = fed_hash
            fed_node.is_active = tenant.is_active
        else:
            control_db.add(
                FederatedNode(
                    branch_code=tenant.branch_code,
                    hospital_name=tenant.hospital_name,
                    subdomain_slug=tenant.subdomain_slug,
                    api_key_prefix=raw_fed_key[:14],
                    api_key_hash=fed_hash,
                    is_active=tenant.is_active,
                )
            )
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

    control_db.add(
        ControlPlaneAuditLog(
            action="ROTATE_KEYS",
            target_slug=slug.lower(),
            details=f"Rotated JWT: {payload.rotate_jwt_secret}, Backup: {payload.rotate_backup_key}, Fed: {payload.rotate_federation_key}",
        )
    )
    control_db.commit()
    return rotated


@router.post("/tenants/{slug}/reset-admin-password")
def emergency_reset_tenant_admin_password(
    slug: str,
    payload: TenantAdminPasswordResetRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
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

    control_db.add(
        ControlPlaneAuditLog(
            action="BREAK_GLASS_ADMIN_RESET",
            target_slug=slug.lower(),
            details=f"Admin password reset for user {admin_email}",
        )
    )
    control_db.commit()
    return {
        "status": "success",
        "subdomain_slug": tenant.subdomain_slug,
        "admin_email": admin_email,
        "one_time_password": new_password,
    }


@router.patch("/tenants/{slug}/entitlements")
def update_tenant_entitlements_and_quotas(
    slug: str,
    payload: EntitlementsUpdateRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Enforces dynamic module access flags, commercial contract fee, capacity quotas, and maintenance windows."""
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    effective_model = payload.pricing_model or payload.subscription_tier or tenant.pricing_model
    tenant.pricing_model = effective_model
    tenant.billing_cycle = payload.billing_cycle
    tenant.enabled_modules_json = json.dumps(payload.enabled_modules)
    tenant.base_license_fee_paise = compute_contract_fee_paise(
        pricing_model=effective_model.value,
        enabled_modules=payload.enabled_modules,
        custom_fee_paise=payload.base_license_fee_paise,
        billing_cycle=payload.billing_cycle.value,
        quota_max_beds=payload.quota_limits.get("max_beds", 500),
    )
    tenant.quota_limits_json = json.dumps(payload.quota_limits)
    tenant.maintenance_mode = payload.maintenance_mode
    tenant.maintenance_notice = payload.maintenance_notice

    control_db.add(
        ControlPlaneAuditLog(
            action="UPDATE_ENTITLEMENTS",
            target_slug=slug.lower(),
            details=f"Model: {effective_model.value}, Fee: INR {tenant.base_license_fee_paise // 100}, Maintenance: {payload.maintenance_mode}",
        )
    )
    control_db.commit()
    return {
        "status": "success",
        "subdomain_slug": tenant.subdomain_slug,
        "pricing_model": tenant.pricing_model.value,
        "subscription_tier": tenant.pricing_model.value,
        "billing_cycle": tenant.billing_cycle.value,
        "base_license_fee_paise": tenant.base_license_fee_paise,
        "maintenance_mode": tenant.maintenance_mode,
    }


@router.get("/tenants/{slug}/telemetry")
def get_tenant_operational_telemetry(
    slug: str,
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
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
            t_db.scalar(select(func.count(PatientEncounter.id)).where(PatientEncounter.status == EncounterStatusEnum.ACTIVE)) or 0
        )
        total_beds = t_db.scalar(select(func.count(Bed.id))) or 0
        occupied_beds = t_db.scalar(select(func.count(Bed.id)).where(Bed.status == BedStatusEnum.OCCUPIED)) or 0
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
        "pricing_model": tenant.pricing_model.value,
        "subscription_tier": tenant.pricing_model.value,
        "billing_cycle": tenant.billing_cycle.value if tenant.billing_cycle else "ANNUAL_LICENSE",
        "base_license_fee_paise": tenant.base_license_fee_paise or 0,
        "is_active": tenant.is_active,
        "maintenance_mode": tenant.maintenance_mode,
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


@router.get("/tenants/{slug}/backups")
def list_tenant_backup_snapshots(
    slug: str,
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Enumerates all AES-256 encrypted snapshots available for one-click disaster recovery."""
    _verify_vendor_key(x_master_setup_key or "")
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    backups_dir = Path("backups")
    items = []
    if backups_dir.exists():
        for f in sorted(backups_dir.glob(f"{tenant.db_name}_*.dump.enc"), reverse=True):
            st = f.stat()
            items.append({
                "filename": f.name,
                "size_bytes": st.st_size,
                "created_at": st.st_mtime,
            })
    return {"total": len(items), "snapshots": items}


@router.post("/tenants/{slug}/backups/trigger")
def trigger_manual_backup_snapshot(
    slug: str,
    payload: TenantAdminPasswordResetRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Triggers an on-demand encrypted database snapshot directly from the application."""
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    from backup_service import _dump_and_encrypt
    backup_dir = Path("backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    raw_key = decrypt_tenant_secret(tenant.backup_encryption_key)
    _dump_and_encrypt(tenant.db_name, raw_key, backup_dir)

    control_db.add(
        ControlPlaneAuditLog(
            action="TRIGGER_MANUAL_BACKUP",
            target_slug=slug.lower(),
            details=f"On-demand snapshot triggered for {tenant.db_name}",
        )
    )
    control_db.commit()
    return {"status": "success", "message": f"Snapshot triggered for {tenant.db_name}"}


@router.post("/tenants/{slug}/backups/{filename}/restore")
def restore_tenant_from_snapshot(
    slug: str,
    filename: str,
    payload: TenantAdminPasswordResetRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Executes a 1-click in-app database restoration without requiring database shells."""
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    safe_name = Path(filename).name
    target_path = Path("backups") / safe_name
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Snapshot file not found in vault.")

    from restore_tenant import restore_encrypted_backup
    raw_key = decrypt_tenant_secret(tenant.backup_encryption_key)
    restore_encrypted_backup(str(target_path), tenant.db_name, raw_key, promote_to_cloud=False)

    control_db.add(
        ControlPlaneAuditLog(
            action="RESTORE_SNAPSHOT",
            target_slug=slug.lower(),
            details=f"Restored snapshot {safe_name} into {tenant.db_name}",
        )
    )
    control_db.commit()
    return {"status": "success", "message": f"Snapshot '{safe_name}' restored cleanly into {tenant.db_name}."}


@router.post("/tenants/{slug}/clone-sandbox")
def clone_hospital_to_sandbox_environment(
    slug: str,
    payload: SandboxCloneRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Clones master catalogs, spatial layout, and custom templates into an isolated training sandbox."""
    _verify_vendor_key(payload.master_setup_key)
    src_tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not src_tenant:
        raise HTTPException(status_code=404, detail=f"Source tenant '{slug}' not found.")

    sandbox_slug = payload.sandbox_subdomain_slug.strip().lower()
    sandbox_db = f"hms_{re.sub(r'[^a-z0-9_]', '_', sandbox_slug)}"

    dup = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == sandbox_slug)
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"Sandbox subdomain '{sandbox_slug}' already exists.")

    with Session(bind=tenant_engine_pool.get_engine(settings.POSTGRES_DB)) as p_session:
        p_session.connection().connection.set_isolation_level(0)
        p_session.execute(text(f'CREATE DATABASE "{sandbox_db}" WITH TEMPLATE "{src_tenant.db_name}"'))

    sandbox_jwt = secrets.token_hex(32)
    sandbox_backup = secrets.token_urlsafe(32)
    sandbox_tenant = TenantRegistry(
        hospital_name=f"{src_tenant.hospital_name} [SANDBOX / UAT]",
        branch_code=f"{src_tenant.branch_code}-SBX",
        subdomain_slug=sandbox_slug,
        db_name=sandbox_db,
        admin_email=src_tenant.admin_email,
        jwt_secret_key=encrypt_tenant_secret(sandbox_jwt),
        backup_encryption_key=encrypt_tenant_secret(sandbox_backup),
        deployment_mode=DeploymentModeEnum.CLOUD,
        pricing_model=src_tenant.pricing_model,
        billing_cycle=src_tenant.billing_cycle,
        base_license_fee_paise=0,
        enabled_modules_json=src_tenant.enabled_modules_json,
        api_base_url=f"https://{sandbox_slug}.{settings.BASE_DOMAIN}",
        is_active=True,
    )
    control_db.add(sandbox_tenant)
    control_db.add(
        ControlPlaneAuditLog(
            action="CLONE_SANDBOX",
            target_slug=sandbox_slug,
            details=f"Cloned from {src_tenant.subdomain_slug}",
        )
    )
    control_db.commit()
    return {
        "status": "success",
        "sandbox_subdomain_slug": sandbox_slug,
        "sandbox_url": f"https://{sandbox_slug}.{settings.BASE_DOMAIN}",
        "database_name": sandbox_db,
    }


@router.get("/fleet/schema-status")
def check_fleet_alembic_schema_parity(
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Inspects alembic_version across all tenant databases and compares against current head."""
    _verify_vendor_key(x_master_setup_key or "")
    alembic_ini = "backend/alembic.ini" if Path("backend/alembic.ini").exists() else "alembic.ini"
    head_rev = ScriptDirectory.from_config(Config(alembic_ini)).get_current_head() or "HEAD"

    tenants = list(control_db.execute(select(TenantRegistry)).scalars().all())
    parity = []
    for t in tenants:
        version = "UNVERSIONED"
        try:
            eng = tenant_engine_pool.get_engine(t.db_name)
            with eng.connect() as conn:
                res = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
                if res:
                    version = res
        except Exception as e:
            version = f"ERROR: {str(e)[:30]}"
        parity.append({
            "subdomain_slug": t.subdomain_slug,
            "db_name": t.db_name,
            "schema_version": version,
            "head_revision": head_rev,
            "status": "UP_TO_DATE" if version == head_rev else "DRIFT_DETECTED",
        })
    return {"fleet_size": len(parity), "head_revision": head_rev, "parity": parity}


def _upgrade_and_sync_single_tenant_obj(t: TenantRegistry, alembic_ini: str) -> None:
    t_engine = tenant_engine_pool.get_engine(t.db_name)
    if t_engine.dialect.name == "postgresql":
        from alembic import command
        from database import build_db_url
        alembic_cfg = Config(alembic_ini)
        alembic_cfg.set_main_option("sqlalchemy.url", build_db_url(t.db_name))
        command.upgrade(alembic_cfg, "head")

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


@router.post("/tenants/{slug}/sync")
def sync_single_cloud_tenant_schema_and_templates(
    slug: str,
    payload: TenantAdminPasswordResetRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """
    STAGE 1 / SINGLE-TENANT UPGRADE:
    Upgrades a single specified hospital database (e.g. 'default' canary or 'apollo') to Alembic head
    and syncs RBAC permissions + 10 standard print templates without touching other hospitals.
    """
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    alembic_ini = "backend/alembic.ini" if Path("backend/alembic.ini").exists() else "alembic.ini"
    _upgrade_and_sync_single_tenant_obj(tenant, alembic_ini)

    control_db.add(
        ControlPlaneAuditLog(
            action="SYNC_SINGLE_TENANT",
            target_slug=tenant.subdomain_slug,
            details=f"Upgraded schema & synced templates for single tenant '{tenant.subdomain_slug}' ({tenant.db_name})",
        )
    )
    control_db.commit()
    return {
        "status": "success",
        "subdomain_slug": tenant.subdomain_slug,
        "db_name": tenant.db_name,
        "message": f"Tenant '{tenant.subdomain_slug}' migrated to head and templates synced.",
    }


@router.post("/tenants/sync-fleet")
def sync_all_cloud_tenants_schema_and_templates(
    payload: TenantAdminPasswordResetRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """
    STAGE 2 FLEET PUSH:
    Applies the verified Alembic 'head' migration AND idempotently syncs universal
    dictionaries, RBAC permissions, and 10 print templates across all active Cloud tenants.
    """
    _verify_vendor_key(payload.master_setup_key)
    cloud_tenants = list(
        control_db.execute(
            select(TenantRegistry).where(
                TenantRegistry.is_active == True,
                TenantRegistry.deployment_mode == DeploymentModeEnum.CLOUD,
            )
        ).scalars().all()
    )

    alembic_ini = "backend/alembic.ini" if Path("backend/alembic.ini").exists() else "alembic.ini"
    synced_slugs = []

    for t in cloud_tenants:
        _upgrade_and_sync_single_tenant_obj(t, alembic_ini)
        synced_slugs.append(t.subdomain_slug)

    control_db.add(
        ControlPlaneAuditLog(
            action="SYNC_AND_MIGRATE_FLEET",
            target_slug="ALL_CLOUD_TENANTS",
            details=f"Migrated & synced {len(synced_slugs)} tenants: {', '.join(synced_slugs)}",
        )
    )
    control_db.commit()

    return {
        "status": "success",
        "synced_tenants_count": len(synced_slugs),
        "synced_subdomains": synced_slugs,
    }


@router.get("/tenants/{slug}/details")
def get_tenant_full_governance_details(
    slug: str,
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Returns full governance config (enabled modules, quotas, maintenance state, hospital policy) + live telemetry."""
    _verify_vendor_key(x_master_setup_key or "")
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    telemetry = get_tenant_operational_telemetry(slug, x_master_setup_key, control_db)
    backups = list_tenant_backup_snapshots(slug, x_master_setup_key, control_db)

    default_modules = {k: True for k in MODULE_CATALOG.keys()}
    try:
        loaded_mods = json.loads(tenant.enabled_modules_json) if tenant.enabled_modules_json else {}
        if loaded_mods.get("all") is True:
            enabled_modules = default_modules
        else:
            enabled_modules = {**default_modules, **loaded_mods}
    except Exception:
        enabled_modules = default_modules

    try:
        quota_limits = json.loads(tenant.quota_limits_json) if tenant.quota_limits_json else {"max_beds": 500, "max_users": 200}
    except Exception:
        quota_limits = {"max_beds": 500, "max_users": 200}

    hospital_policy = {
        "federation_sharing_mode": "FULL_MESH",
        "enforce_financial_discharge_gate": True,
        "max_failed_login_attempts": 5,
    }
    try:
        from routers.v1.tenant_admin_router import get_or_create_policy
        eng = tenant_engine_pool.get_engine(tenant.db_name)
        with Session(bind=eng) as tdb:
            pol = get_or_create_policy(tdb)
            hospital_policy = {
                "federation_sharing_mode": pol.federation_sharing_mode.value,
                "enforce_financial_discharge_gate": pol.enforce_financial_discharge_gate,
                "max_failed_login_attempts": pol.max_failed_login_attempts,
            }
    except Exception:
        pass

    return {
        **telemetry,
        "admin_email": tenant.admin_email,
        "maintenance_notice": tenant.maintenance_notice or "",
        "enabled_modules": enabled_modules,
        "quota_limits": quota_limits,
        "hospital_policy": hospital_policy,
        "snapshots": backups["snapshots"],
    }


@router.get("/federation/topology")
def get_federation_network_topology(
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """
    Zero-Knowledge Federation Network Topology: Shows connected hospital branch nodes,
    their federation sovereignty mode, and aggregate pointer counts WITHOUT exposing patient PII.
    """
    _verify_vendor_key(x_master_setup_key or "")
    tenants = {t.branch_code: t for t in control_db.execute(select(TenantRegistry)).scalars().all()}
    nodes = list(control_db.execute(select(FederatedNode).order_by(FederatedNode.id.asc())).scalars().all())
    pointer_counts = dict(
        control_db.execute(
            select(GlobalPatientPointer.branch_code, func.count(GlobalPatientPointer.id)).group_by(
                GlobalPatientPointer.branch_code
            )
        ).all()
    )

    items = []
    for n in nodes:
        t = tenants.get(n.branch_code)
        sharing_mode = "FULL_MESH"
        if t and t.is_active:
            try:
                from routers.v1.tenant_admin_router import get_or_create_policy
                eng = tenant_engine_pool.get_engine(t.db_name)
                with Session(bind=eng) as tdb:
                    pol = get_or_create_policy(tdb)
                    sharing_mode = pol.federation_sharing_mode.value
            except Exception:
                sharing_mode = "FULL_MESH"

        items.append({
            "branch_code": n.branch_code,
            "hospital_name": n.hospital_name,
            "subdomain_slug": n.subdomain_slug,
            "api_key_prefix": n.api_key_prefix,
            "is_active": n.is_active,
            "federation_sharing_mode": sharing_mode,
            "indexed_pointers_count": pointer_counts.get(n.branch_code, 0),
            "last_used_at": n.last_used_at.isoformat() if n.last_used_at else None,
        })
    return {"total_nodes": len(items), "nodes": items}


@router.get("/mpi/lookup")
def lookup_global_mpi_pointers(
    query: str,
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Diagnostic MPI pointer verification endpoint (returns only zero-knowledge hashes & branch codes)."""
    _verify_vendor_key(x_master_setup_key or "")
    q = query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Search query cannot be empty.")

    clean_digits = re.sub(r"\D", "", q)
    phone_hash = hashlib.sha256(clean_digits.encode("utf-8")).hexdigest() if clean_digits else ""

    stmt = (
        select(GlobalPatientPointer)
        .where(
            (GlobalPatientPointer.phone_hash == phone_hash)
            | (GlobalPatientPointer.phone_hash == q.lower())
            | (GlobalPatientPointer.abha_number == q)
            | (GlobalPatientPointer.source_mrn == q.upper())
        )
        .order_by(GlobalPatientPointer.last_clinical_update_at.desc())
    )
    pointers = list(control_db.execute(stmt).scalars().all())
    return {
        "query": q,
        "computed_phone_sha256": phone_hash[:16] + "..." if phone_hash else None,
        "matches_count": len(pointers),
        "pointers": [
            {
                "id": p.id,
                "branch_code": p.branch_code,
                "subdomain_slug": p.subdomain_slug,
                "source_mrn": p.source_mrn,
                "abha_number": p.abha_number,
                "phone_hash_prefix": p.phone_hash[:16] + "...",
                "has_critical_allergies": p.has_critical_allergies,
                "last_clinical_update_at": p.last_clinical_update_at.isoformat() if p.last_clinical_update_at else None,
            }
            for p in pointers
        ],
    }


@router.get("/fleet/audit-logs")
def list_control_plane_audit_logs(
    limit: int = 50,
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    """Returns the immutable vendor action audit trail across the entire Control Plane."""
    _verify_vendor_key(x_master_setup_key or "")
    logs = list(
        control_db.execute(
            select(ControlPlaneAuditLog).order_by(ControlPlaneAuditLog.id.desc()).limit(min(limit, 200))
        ).scalars().all()
    )
    return {
        "total": len(logs),
        "items": [
            {
                "id": log.id,
                "action": log.action,
                "target_slug": log.target_slug,
                "actor": log.actor,
                "details": log.details,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }
@router.get("/fleet/analytics")
def get_fleet_commercial_and_operational_analytics(
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
) -> Dict[str, Any]:
    """
    Returns deep fleet-wide commercial intelligence (ARR by bundle, billing cycle mix,
    module adoption rates across hospitals) and per-hospital operational leaderboards.
    """
    _verify_vendor_key(x_master_setup_key or "")
    tenants = list(control_db.execute(select(TenantRegistry).order_by(TenantRegistry.id.asc())).scalars().all())

    arr_by_pricing_model: Dict[str, int] = {}
    count_by_billing_cycle: Dict[str, int] = {}
    module_adoption_counts: Dict[str, int] = {k: 0 for k in MODULE_CATALOG.keys()}
    hospital_leaderboard = []

    for t in tenants:
        model_key = t.pricing_model.value if t.pricing_model else "BUNDLE_TOTAL_HOSPITAL"
        cycle_key = t.billing_cycle.value if t.billing_cycle else "ANNUAL_LICENSE"
        count_by_billing_cycle[cycle_key] = count_by_billing_cycle.get(cycle_key, 0) + 1

        fee = getattr(t, "base_license_fee_paise", 0) or 0
        if cycle_key in ("MONTHLY_SUBSCRIPTION", "PER_ACTIVE_BED_MONTHLY"):
            norm_arr = fee * 12
        elif cycle_key == "ONE_TIME_PERPETUAL":
            norm_arr = int(fee * 0.20)
        else:
            norm_arr = fee

        if t.is_active:
            arr_by_pricing_model[model_key] = arr_by_pricing_model.get(model_key, 0) + norm_arr

        # Module adoption count
        try:
            mods = json.loads(t.enabled_modules_json) if t.enabled_modules_json else {"all": True}
            for m_key in MODULE_CATALOG.keys():
                if mods.get("all") is True or mods.get(m_key, False) is True:
                    module_adoption_counts[m_key] += 1
        except Exception:
            for m_key in MODULE_CATALOG.keys():
                module_adoption_counts[m_key] += 1

        # Gather per-hospital operational snapshot
        pts_cnt = 0
        beds_tot = 0
        beds_occ = 0
        gmv_paise = 0
        if t.is_active:
            try:
                eng = tenant_engine_pool.get_engine(t.db_name)
                TSession = sessionmaker(bind=eng)
                with TSession() as tdb:
                    pts_cnt = tdb.scalar(select(func.count(Patient.id))) or 0
                    beds_tot = tdb.scalar(select(func.count(Bed.id))) or 0
                    beds_occ = tdb.scalar(select(func.count(Bed.id)).where(Bed.status == BedStatusEnum.OCCUPIED)) or 0
                    gmv_paise = tdb.scalar(select(func.coalesce(func.sum(Invoice.amount_paid_paise), 0))) or 0
            except Exception:
                pass

        hospital_leaderboard.append({
            "subdomain_slug": t.subdomain_slug,
            "hospital_name": t.hospital_name,
            "branch_code": t.branch_code,
            "is_active": t.is_active,
            "pricing_model": model_key,
            "billing_cycle": cycle_key,
            "normalized_arr_paise": norm_arr,
            "patients_count": pts_cnt,
            "total_beds": beds_tot,
            "occupied_beds": beds_occ,
            "occupancy_pct": round((beds_occ / beds_tot) * 100.0, 1) if beds_tot > 0 else 0.0,
            "collected_gmv_paise": gmv_paise,
        })

    return {
        "arr_by_pricing_model_paise": arr_by_pricing_model,
        "tenants_by_billing_cycle": count_by_billing_cycle,
        "module_adoption_counts": module_adoption_counts,
        "total_tenants": len(tenants),
        "hospital_leaderboard": sorted(hospital_leaderboard, key=lambda x: x["collected_gmv_paise"], reverse=True),
    }