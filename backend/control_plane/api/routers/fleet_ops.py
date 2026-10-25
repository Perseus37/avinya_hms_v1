import hashlib
import json
import logging
from pathlib import Path
import re
import secrets
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, Optional

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.domains.billing.models import InvoiceStatusEnum
from backend.core.config import settings
from backend.control_plane.models import (
    BillingCycleEnum,
    ControlPlaneAuditLog,
    DeploymentModeEnum,
    FederatedNode,
    GlobalPatientPointer,
    PricingModelEnum,
    TenantRegistry,
)
from backend.control_plane.services.pricing import (
    compute_contract_fee_paise,
    get_module_catalog,
    normalize_contract_arr_paise,
)
from backend.core.database import ControlSessionLocal, get_control_db, tenant_engine_pool
from backend.domains.billing.models import Invoice
from backend.domains.encounters.models import EncounterStatusEnum, PatientEncounter
from backend.domains.facility.models import Bed, BedStatusEnum
from backend.domains.identity.models import User, UserStatusEnum
from backend.domains.identity.passwords import pwd_context
from backend.domains.patients.models import Patient
from backend.domains.federation.models import FederatedClient
from backend.security.envelope import decrypt_tenant_secret, encrypt_tenant_secret
from backend.core.seed import seed_universal_hospital_data
from backend.control_plane.services.auth import (
    verify_control_plane_secret,
    _verify_vendor_key,
    require_operator_role,
)
from backend.control_plane.services.sandbox_sanitizer import scrub_sandbox_database
from backend.scripts.migrate import upgrade_tenant_database_url

logger = logging.getLogger("avinya.fleet_ops")
router = APIRouter(prefix="/api", tags=["Control Plane Fleet Operations"])


class TenantStatusUpdateRequest(BaseModel):
    master_setup_key: str
    is_active: bool

class TenantDeploymentModeUpdateRequest(BaseModel):
    master_setup_key: str
    deployment_mode: DeploymentModeEnum

class TenantKeyRotationRequest(BaseModel):
    master_setup_key: str
    rotate_jwt_secret: bool = True
    rotate_backup_key: bool = True
    rotate_federation_key: bool = True

class TenantAdminPasswordResetRequest(BaseModel):
    master_setup_key: str

class EntitlementsUpdateRequest(BaseModel):
    master_setup_key: str
    pricing_model: str = "BUNDLE_TOTAL_HOSPITAL"
    billing_cycle: str = "ANNUAL_LICENSE"
    base_license_fee_paise: Optional[int] = None
    enabled_modules: Dict[str, bool]
    quota_limits: Dict[str, int]
    federation_sharing_mode: Optional[str] = None
    maintenance_mode: bool = False
    maintenance_notice: Optional[str] = None

class SandboxCloneRequest(BaseModel):
    master_setup_key: str
    sandbox_subdomain_slug: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9\-]+$")

class SaasLicenseChargeRequest(BaseModel):
    master_setup_key: str
    amount_paid_paise: int = Field(gt=0)
    payment_method: str = "BANK_NEFT_RTGS"
    transaction_reference: str = Field(min_length=3, max_length=100)
    notes: Optional[str] = None

def _terminate_db_connections(db: Session, db_name: str) -> None:
    db.execute(
        text("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = :db_name AND pid <> pg_backend_pid();
        """),
        {"db_name": db_name}
    )
    db.commit()

@router.get("/fleet/overview")
def get_fleet_global_overview(
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
) -> Dict[str, Any]:
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
        cycle = getattr(t, "billing_cycle_string", None) or "ANNUAL_LICENSE"
        total_arr_paise += normalize_contract_arr_paise(fee, cycle)

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
    dynamic_modules = get_module_catalog(control_db)
    for t in tenants:
        fn = fed_nodes.get(t.branch_code)
        try:
            mods = json.loads(t.enabled_modules_json) if t.enabled_modules_json else {"all": True}
            active_mod_count = len(dynamic_modules) if mods.get("all") is True else sum(1 for v in mods.values() if v)
        except Exception:
            active_mod_count = len(dynamic_modules)

        pricing_model_val = getattr(t, "pricing_model_string", "BUNDLE_TOTAL_HOSPITAL")
        billing_cycle_val = getattr(t, "billing_cycle_string", "ANNUAL_LICENSE")
        try:
            provisioning_config = (
                json.loads(t.provisioning_config_json)
                if t.provisioning_config_json
                else {}
            )
        except json.JSONDecodeError:
            provisioning_config = {}
        try:
            quota_limits = (
                json.loads(t.quota_limits_json)
                if t.quota_limits_json
                else {"max_beds": 500, "max_users": 200}
            )
        except json.JSONDecodeError:
            quota_limits = {"max_beds": 500, "max_users": 200}

        items.append(
            {
                "id": t.id,
                "hospital_name": t.hospital_name,
                "branch_code": t.branch_code,
                "subdomain_slug": t.subdomain_slug,
                "tenant_url": settings.tenant_url(t.subdomain_slug),
                "db_name": t.db_name,
                "admin_email": t.admin_email,
                "deployment_mode": t.deployment_mode.value,
                "pricing_model": pricing_model_val,
                "billing_cycle": billing_cycle_val,
                "base_license_fee_paise": t.base_license_fee_paise or 0,
                "quota_limits": quota_limits,
                "provisioning_config": provisioning_config,
                "blueprint_archetype": provisioning_config.get("blueprint_archetype"),
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
    _role: str = Depends(require_operator_role("ops", "superadmin")),
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
    _role: str = Depends(require_operator_role("support", "ops", "superadmin")),
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
        admin_user.password_changed_at = datetime.now(timezone.utc)

        # R-07: Invalidate all active sessions and reset tokens for this admin
        from backend.domains.identity.models import AuthSession, PasswordResetToken
        t_db.execute(delete(AuthSession).where(AuthSession.user_id == admin_user.id))
        t_db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == admin_user.id))
        t_db.commit()
        admin_email = admin_user.email
    control_db.add(
        ControlPlaneAuditLog(
            action="BREAK_GLASS_ADMIN_RESET",
            target_slug=slug.lower(),
            details=f"Admin password reset for user {admin_email}. Active sessions invalidated.",
        )
    )
    control_db.commit()
    return {
        "status": "success",
        "subdomain_slug": tenant.subdomain_slug,
        "admin_email": admin_email,
        "one_time_password": new_password,
        "sessions_invalidated": True,
    }

@router.patch("/tenants/{slug}/entitlements")
def update_tenant_entitlements_and_quotas(
    slug: str,
    payload: EntitlementsUpdateRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    tenant.pricing_model_string = payload.pricing_model
    tenant.billing_cycle_string = payload.billing_cycle
    tenant.enabled_modules_json = json.dumps(payload.enabled_modules)

    tenant.base_license_fee_paise = compute_contract_fee_paise(
        db=control_db,
        pricing_model=payload.pricing_model,
        enabled_modules=payload.enabled_modules,
        custom_fee_paise=payload.base_license_fee_paise,
        billing_cycle=payload.billing_cycle,
        quota_max_beds=payload.quota_limits.get("max_beds", 500),
    )

    tenant.quota_limits_json = json.dumps(payload.quota_limits)
    tenant.maintenance_mode = payload.maintenance_mode
    tenant.maintenance_notice = payload.maintenance_notice

    control_db.add(
        ControlPlaneAuditLog(
            action="UPDATE_ENTITLEMENTS",
            target_slug=slug.lower(),
            details=f"Model: {payload.pricing_model}, Fee: INR {tenant.base_license_fee_paise // 100}, Maintenance: {payload.maintenance_mode}",
        )
    )
    control_db.commit()

    return {
        "status": "success",
        "subdomain_slug": tenant.subdomain_slug,
        "pricing_model": getattr(tenant, "pricing_model_string", "BUNDLE_TOTAL_HOSPITAL"),
        "billing_cycle": getattr(tenant, "billing_cycle_string", "ANNUAL_LICENSE"),
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
        invoices_count = (
            t_db.scalar(select(func.count(Invoice.id)).where(Invoice.status != InvoiceStatusEnum.VOID)) or 0
        )
        gross_revenue_paise = (
            t_db.scalar(
                select(func.coalesce(func.sum(Invoice.total_amount_paise), 0)).where(
                    Invoice.status != InvoiceStatusEnum.VOID
                )
            )
            or 0
        )
        collected_revenue_paise = (
            t_db.scalar(
                select(func.coalesce(func.sum(Invoice.amount_paid_paise), 0)).where(
                    Invoice.status != InvoiceStatusEnum.VOID
                )
            )
            or 0
        )
    backups_dir = Path("backups")
    backups = sorted(backups_dir.glob(f"{tenant.db_name}_*.dump.enc")) if backups_dir.exists() else []

    pricing_model_val = getattr(tenant, "pricing_model_string", "BUNDLE_TOTAL_HOSPITAL")
    billing_cycle_val = getattr(tenant, "billing_cycle_string", "ANNUAL_LICENSE")

    return {
        "subdomain_slug": tenant.subdomain_slug,
        "hospital_name": tenant.hospital_name,
        "branch_code": tenant.branch_code,
        "db_name": tenant.db_name,
        "deployment_mode": tenant.deployment_mode.value,
        "pricing_model": pricing_model_val,
        "subscription_tier": pricing_model_val,
        "billing_cycle": billing_cycle_val,
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
    _verify_vendor_key(x_master_setup_key or "")
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    backups_dir = Path("backups")
    items = []
    if backups_dir.exists():
        for f in sorted(
            backups_dir.glob(f"{tenant.db_name}_*.dump.enc"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ):
            st = f.stat()
            items.append({
                "filename": f.name,
                "size_bytes": st.st_size,
                "created_at": st.st_mtime,
            })
    return {"total": len(items), "snapshots": items}


def _record_backup_task_audit(action: str, slug: str, details: str) -> None:
    try:
        with ControlSessionLocal() as audit_db:
            audit_db.add(
                ControlPlaneAuditLog(
                    action=action,
                    target_slug=slug,
                    details=details[:1000],
                )
            )
            audit_db.commit()
    except Exception:
        logger.exception("Failed to persist backup task audit event for tenant %s", slug)


@router.post("/tenants/{slug}/backups/trigger")
def trigger_manual_backup_snapshot(
    slug: str,
    payload: TenantAdminPasswordResetRequest,
    background_tasks: BackgroundTasks,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
    _role: str = Depends(require_operator_role("ops", "superadmin")),
):
    _verify_vendor_key(payload.master_setup_key)
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    from backend.scripts.backup_service import _dump_and_encrypt
    backup_dir = Path("backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    raw_key = decrypt_tenant_secret(tenant.backup_encryption_key)

    # FIX: Offload synchronous pg_dump processing to background threads to prevent UI hangs
    tenant_db_name = tenant.db_name
    tenant_slug = tenant.subdomain_slug

    def _execute_backup():
        try:
            snapshot = _dump_and_encrypt(tenant_db_name, raw_key, backup_dir)
            _record_backup_task_audit(
                "BACKUP_COMPLETED",
                tenant_slug,
                f"Encrypted snapshot created: {snapshot.name} ({snapshot.stat().st_size} bytes)",
            )
        except Exception as exc:
            logger.exception("Manual backup failed for tenant %s", tenant_slug)
            _record_backup_task_audit(
                "BACKUP_FAILED",
                tenant_slug,
                f"Manual backup failed ({type(exc).__name__}). Check service logs for request context.",
            )
            raise

    background_tasks.add_task(_execute_backup)

    control_db.add(
        ControlPlaneAuditLog(
            action="TRIGGER_MANUAL_BACKUP",
            target_slug=slug.lower(),
            details=f"On-demand background snapshot queued for {tenant.db_name}",
        )
    )
    control_db.commit()
    return {"status": "success", "message": f"Snapshot queued for {tenant.db_name}"}

@router.post("/tenants/{slug}/backups/{filename}/restore")
def restore_tenant_from_snapshot(
    slug: str,
    filename: str,
    payload: TenantAdminPasswordResetRequest,
    background_tasks: BackgroundTasks,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
    _role: str = Depends(require_operator_role("ops", "superadmin")),
):
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

    _terminate_db_connections(control_db, tenant.db_name)

    from backend.scripts.restore_tenant import restore_encrypted_backup
    encrypted_backup_key = tenant.backup_encryption_key

    tenant_db_name = tenant.db_name
    tenant_slug = tenant.subdomain_slug

    def _execute_restore():
        try:
            # R-08: Automatic pre-restore safety snapshot before destructive restore
            try:
                from backend.scripts.backup_service import create_encrypted_backup
                safety_path = create_encrypted_backup(tenant_db_name, encrypted_backup_key)
                _record_backup_task_audit(
                    "PRE_RESTORE_SAFETY_SNAPSHOT",
                    tenant_slug,
                    f"Created pre-restore safety snapshot {Path(safety_path).name} before overwriting {tenant_db_name}.",
                )
            except Exception as snap_exc:
                logger.warning("Could not create pre-restore safety snapshot for %s: %s", tenant_slug, snap_exc)

            restore_encrypted_backup(
                str(target_path), tenant_db_name, encrypted_backup_key, promote_to_cloud=False
            )
            _record_backup_task_audit(
                "RESTORE_COMPLETED",
                tenant_slug,
                f"Snapshot {safe_name} restored into {tenant_db_name}.",
            )
        except Exception as exc:
            logger.exception("Snapshot restore failed for tenant %s", tenant_slug)
            _record_backup_task_audit(
                "RESTORE_FAILED",
                tenant_slug,
                f"Snapshot {safe_name} restore failed ({type(exc).__name__}). Check service logs for request context.",
            )
            raise

    background_tasks.add_task(_execute_restore)

    control_db.add(
        ControlPlaneAuditLog(
            action="RESTORE_SNAPSHOT",
            target_slug=slug.lower(),
            details=f"Queued restoration of {safe_name} into {tenant.db_name}",
        )
    )
    control_db.commit()
    return {"status": "success", "message": f"Snapshot '{safe_name}' queued for restoration into {tenant.db_name}."}

def _scrub_sandbox_tenant_data(sandbox_db: str) -> None:
    """
    R-02 / F-02: Scrubs and anonymizes all production patient and user data in the newly cloned sandbox database.
    Prevents live patient PII/PHI, passwords, MFA secrets, or real staff contact info from persisting in UAT/sandbox environments.
    """
    scrub_sandbox_database(sandbox_db)


@router.post("/tenants/{slug}/clone-sandbox")
def clone_hospital_to_sandbox_environment(
    slug: str,
    payload: SandboxCloneRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
    _role: str = Depends(require_operator_role("ops", "superadmin")),
):
    _verify_vendor_key(payload.master_setup_key)
    src_tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()
    if not src_tenant:
        raise HTTPException(status_code=404, detail=f"Source tenant '{slug}' not found.")
    if not src_tenant.is_active:
        raise HTTPException(status_code=400, detail=f"Source tenant '{slug}' is not active.")

    sandbox_slug = payload.sandbox_subdomain_slug.strip().lower()
    sandbox_db = (
        f"{settings.TENANT_DATABASE_PREFIX}"
        f"{re.sub(r'[^a-z0-9_]', '_', sandbox_slug)}"
    )

    dup = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == sandbox_slug)
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"Sandbox subdomain '{sandbox_slug}' already exists.")

    db_exists = control_db.execute(
        text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
        {"db_name": sandbox_db}
    ).scalar()
    if db_exists:
        raise HTTPException(status_code=409, detail=f"Database '{sandbox_db}' already exists on disk.")

    _terminate_db_connections(control_db, src_tenant.db_name)

    with Session(bind=tenant_engine_pool.get_engine(settings.POSTGRES_DB)) as p_session:
        p_session.connection().connection.set_isolation_level(0)
        p_session.execute(text(f'CREATE DATABASE "{sandbox_db}" WITH TEMPLATE "{src_tenant.db_name}"'))

    # F-02: Scrub production patient PII/PHI and sensitive user contacts from cloned sandbox database
    _scrub_sandbox_tenant_data(sandbox_db)

    sandbox_jwt = secrets.token_hex(32)
    sandbox_backup = secrets.token_urlsafe(32)

    pricing_model_val = getattr(src_tenant, "pricing_model_string", "BUNDLE_TOTAL_HOSPITAL")
    billing_cycle_val = getattr(src_tenant, "billing_cycle_string", "ANNUAL_LICENSE")

    sandbox_tenant = TenantRegistry(
        hospital_name=f"{src_tenant.hospital_name} [SANDBOX / UAT]",
        branch_code=f"{src_tenant.branch_code}-SBX",
        subdomain_slug=sandbox_slug,
        db_name=sandbox_db,
        admin_email=src_tenant.admin_email,
        jwt_secret_key=encrypt_tenant_secret(sandbox_jwt),
        backup_encryption_key=encrypt_tenant_secret(sandbox_backup),
        deployment_mode=DeploymentModeEnum.CLOUD,
        pricing_model_string=pricing_model_val,
        billing_cycle_string=billing_cycle_val,
        base_license_fee_paise=0,
        enabled_modules_json=src_tenant.enabled_modules_json,
        api_base_url=settings.tenant_url(sandbox_slug),
        is_active=True,
    )
    control_db.add(sandbox_tenant)
    control_db.add(
        ControlPlaneAuditLog(
            action="CLONE_SANDBOX",
            target_slug=sandbox_slug,
            details=f"Cloned from {src_tenant.subdomain_slug} with automated patient PII/PHI scrubbing",
        )
    )
    control_db.commit()

    return {
        "status": "success",
        "sandbox_subdomain_slug": sandbox_slug,
        "sandbox_url": settings.tenant_url(sandbox_slug),
        "database_name": sandbox_db,
    }

@router.get("/fleet/schema-status")
def check_fleet_alembic_schema_parity(
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
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
        from backend.core.database import build_db_url
        upgrade_tenant_database_url(build_db_url(t.db_name))

    TSession = sessionmaker(bind=t_engine, autocommit=False, autoflush=False)
    with TSession() as t_db:
        seed_universal_hospital_data(
            db=t_db,
            hospital_name=t.hospital_name,
            branch_code=t.branch_code,
            admin_email=t.admin_email,
            admin_password=secrets.token_urlsafe(32),
        )
        t_db.commit()
@router.post("/tenants/{slug}/sync")
def sync_single_cloud_tenant_schema_and_templates(
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
    _verify_vendor_key(x_master_setup_key or "")
    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    telemetry = get_tenant_operational_telemetry(slug, x_master_setup_key, control_db)
    backups = list_tenant_backup_snapshots(slug, x_master_setup_key, control_db)

    dynamic_modules = get_module_catalog(control_db)
    default_modules = {k: True for k in dynamic_modules.keys()}
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

    try:
        provisioning_config = (
            json.loads(tenant.provisioning_config_json)
            if tenant.provisioning_config_json
            else {}
        )
    except json.JSONDecodeError:
        provisioning_config = {}

    hospital_policy = {
        "federation_sharing_mode": "FULL_MESH",
        "enforce_financial_discharge_gate": True,
        "max_failed_login_attempts": 5,
    }

    try:
        from backend.domains.policies.router import get_or_create_policy
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

    pricing_model_val = getattr(tenant, "pricing_model_string", "BUNDLE_TOTAL_HOSPITAL")
    billing_cycle_val = getattr(tenant, "billing_cycle_string", "ANNUAL_LICENSE")

    return {
        **telemetry,
        "admin_email": tenant.admin_email,
        "pricing_model": pricing_model_val,
        "billing_cycle": billing_cycle_val,
        "maintenance_notice": tenant.maintenance_notice or "",
        "enabled_modules": enabled_modules,
        "quota_limits": quota_limits,
        "provisioning_config": provisioning_config,
        "hospital_policy": hospital_policy,
        "snapshots": backups["snapshots"],
    }

@router.get("/federation/topology")
def get_federation_network_topology(
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
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
                from backend.domains.policies.router import get_or_create_policy
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
    request: Request,
    query: str,
    justification: str = Query(..., min_length=10, description="Mandatory operational or clinical justification for cross-tenant search"),
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
    _role: str = Depends(require_operator_role("support", "superadmin")),
):
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

    # R-06: Comprehensive audit logging for all cross-tenant patient index lookups
    actor = getattr(request.state, "operator_email", None) or "VENDOR_MASTER_KEY"
    control_db.add(
        ControlPlaneAuditLog(
            action="MPI_CROSS_TENANT_LOOKUP",
            target_slug="GLOBAL_MPI",
            actor=actor,
            details=f"MPI lookup for hash={phone_hash[:16]}... justification='{justification.strip()}' matches={len(pointers)}",
        )
    )
    control_db.commit()

    return {
        "query": q,
        "justification": justification.strip(),
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
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    action: Optional[str] = Query(default=None, max_length=100),
    target: Optional[str] = Query(default=None, max_length=100),
    x_master_setup_key: Optional[str] = Header(default=None, alias="X-Master-Setup-Key"),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    _verify_vendor_key(x_master_setup_key or "")
    query = select(ControlPlaneAuditLog)
    count_query = select(func.count(ControlPlaneAuditLog.id))
    if action and action.strip():
        query = query.where(ControlPlaneAuditLog.action == action.strip())
        count_query = count_query.where(ControlPlaneAuditLog.action == action.strip())
    if target and target.strip():
        target_filter = ControlPlaneAuditLog.target_slug.ilike(f"%{target.strip()}%")
        query = query.where(target_filter)
        count_query = count_query.where(target_filter)
    total = control_db.scalar(count_query) or 0
    logs = list(
        control_db.execute(
            query.order_by(ControlPlaneAuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(logs) < total,
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
    _verify_vendor_key(x_master_setup_key or "")
    tenants = list(control_db.execute(select(TenantRegistry).order_by(TenantRegistry.id.asc())).scalars().all())

    arr_by_pricing_model: Dict[str, int] = {}
    count_by_billing_cycle: Dict[str, int] = {}
    one_time_license_value_paise = 0
    dynamic_modules = get_module_catalog(control_db)
    module_adoption_counts: Dict[str, int] = {k: 0 for k in dynamic_modules.keys()}
    hospital_leaderboard = []
    database_ready_tenants = 0
    configured_tenants = 0
    first_admin_login_tenants = 0
    first_login_durations_seconds = []

    for t in tenants:
        model_key = getattr(t, "pricing_model_string", "BUNDLE_TOTAL_HOSPITAL")
        cycle_key = getattr(t, "billing_cycle_string", "ANNUAL_LICENSE")

        count_by_billing_cycle[cycle_key] = count_by_billing_cycle.get(cycle_key, 0) + 1
        fee = getattr(t, "base_license_fee_paise", 0) or 0
        norm_arr = normalize_contract_arr_paise(fee, cycle_key)
        if t.is_active and cycle_key == "ONE_TIME_PERPETUAL":
            one_time_license_value_paise += fee

        if t.is_active:
            arr_by_pricing_model[model_key] = arr_by_pricing_model.get(model_key, 0) + norm_arr

        try:
            enabled_modules = (
                json.loads(t.enabled_modules_json)
                if t.enabled_modules_json
                else {"all": True}
            )
            if not isinstance(enabled_modules, dict):
                enabled_modules = {}
        except (TypeError, json.JSONDecodeError):
            enabled_modules = {}
        try:
            quotas = json.loads(t.quota_limits_json) if t.quota_limits_json else {}
            if not isinstance(quotas, dict):
                quotas = {}
        except (TypeError, json.JSONDecodeError):
            quotas = {}
        if t.is_active and enabled_modules and quotas:
            configured_tenants += 1
        adopted_modules = (
            set(dynamic_modules)
            if enabled_modules.get("all") is True
            else {
                code
                for code, is_enabled in enabled_modules.items()
                if is_enabled and code in dynamic_modules
            }
        )
        if t.is_active:
            for module_code in adopted_modules:
                module_adoption_counts[module_code] += 1

        pts_cnt = 0
        beds_tot = 0
        beds_occ = 0
        gmv_paise = 0
        first_admin_login_at = None

        if t.is_active:
            try:
                eng = tenant_engine_pool.get_engine(t.db_name)
                TSession = sessionmaker(bind=eng)
                with TSession() as tdb:
                    pts_cnt = tdb.scalar(select(func.count(Patient.id))) or 0
                    beds_tot = tdb.scalar(select(func.count(Bed.id))) or 0
                    beds_occ = tdb.scalar(select(func.count(Bed.id)).where(Bed.status == BedStatusEnum.OCCUPIED)) or 0
                    gmv_paise = tdb.scalar(select(func.coalesce(func.sum(Invoice.amount_paid_paise), 0))) or 0
                    first_admin_login_at = tdb.scalar(
                        select(User.first_login_at).where(User.email == t.admin_email)
                    )
                database_ready_tenants += 1
                if first_admin_login_at:
                    first_admin_login_tenants += 1
                    created_at = t.created_at
                    if created_at:
                        created_at = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
                        first_admin_login_at = (
                            first_admin_login_at.replace(tzinfo=timezone.utc)
                            if first_admin_login_at.tzinfo is None
                            else first_admin_login_at
                        )
                        first_login_durations_seconds.append(
                            max(0, int((first_admin_login_at - created_at).total_seconds()))
                        )
            except Exception as exc:
                logger.warning(
                    "Onboarding and tenant metrics could not be loaded for %s: %s",
                    t.subdomain_slug,
                    str(exc),
                )
        hospital_leaderboard.append({
            "subdomain_slug": t.subdomain_slug,
            "hospital_name": t.hospital_name,
            "branch_code": t.branch_code,
            "is_active": t.is_active,
            "pricing_model": model_key,
            "billing_cycle": cycle_key,
            "contract_fee_paise": fee,
            "normalized_arr_paise": norm_arr if t.is_active else 0,
            "patients_count": pts_cnt,
            "total_beds": beds_tot,
            "occupied_beds": beds_occ,
            "occupancy_pct": round((beds_occ / beds_tot) * 100.0, 1) if beds_tot > 0 else 0.0,
            "collected_gmv_paise": gmv_paise,
        })

    active_tenants = sum(1 for tenant in tenants if tenant.is_active)
    failed_provisioning_count = control_db.scalar(
        select(func.count(ControlPlaneAuditLog.id)).where(
            ControlPlaneAuditLog.action == "PROVISION_FAILED"
        )
    ) or 0
    failed_welcome_emails_count = control_db.scalar(
        select(func.count(ControlPlaneAuditLog.id)).where(
            ControlPlaneAuditLog.action == "PROVISION_EMAIL_FAILED"
        )
    ) or 0
    return {
        "arr_by_pricing_model_paise": arr_by_pricing_model,
        "annual_recurring_revenue_paise": sum(arr_by_pricing_model.values()),
        "one_time_license_value_paise": one_time_license_value_paise,
        "arr_methodology": (
            "Annual contracts use the contracted annual fee; monthly and "
            "per-bed monthly contracts use the contracted monthly fee x 12; "
            "perpetual licenses are excluded from ARR and reported separately."
        ),
        "tenants_by_billing_cycle": count_by_billing_cycle,
        "module_adoption_counts": module_adoption_counts,
        "total_tenants": len(tenants),
        "active_tenants": active_tenants,
        "onboarding_funnel": {
            "sampled_at": datetime.now(timezone.utc).isoformat(),
            "source": "tenant registry, tenant database readiness, configuration and first-admin login timestamps",
            "stages": [
                {"key": "registered", "label": "Registered", "count": len(tenants)},
                {"key": "database_ready", "label": "Database ready", "count": database_ready_tenants},
                {"key": "configured", "label": "Configured", "count": configured_tenants},
                {"key": "admin_signed_in", "label": "First admin sign-in", "count": first_admin_login_tenants},
                {"key": "active", "label": "Active tenants", "count": active_tenants},
            ],
            "median_seconds_to_first_admin_login": (
                int(median(first_login_durations_seconds))
                if first_login_durations_seconds
                else None
            ),
            "provisioning_failures_recorded": failed_provisioning_count,
            "welcome_email_failures_recorded": failed_welcome_emails_count,
        },
        "hospital_leaderboard": sorted(hospital_leaderboard, key=lambda x: x["collected_gmv_paise"], reverse=True),
    }

@router.post("/tenants/{slug}/saas-billing/charge")
def record_tenant_saas_license_payment(
    slug: str,
    payload: SaasLicenseChargeRequest,
    control_db: Session = Depends(get_control_db),
    _: None = Depends(verify_control_plane_secret),
):
    from datetime import datetime, timezone
    _verify_vendor_key(payload.master_setup_key)

    tenant = control_db.execute(
        select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug.lower())
    ).scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{slug}' not found.")

    try:
        contract = json.loads(tenant.commercial_contract_json) if tenant.commercial_contract_json else {}
    except Exception:
        contract = {}

    payments = contract.get("saas_payments", [])
    entry = {
        "id": f"SAAS-RCPT-{len(payments) + 1:03d}",
        "amount_paid_paise": payload.amount_paid_paise,
        "payment_method": payload.payment_method,
        "transaction_reference": payload.transaction_reference,
        "notes": payload.notes or "License Subscription Payment",
        "paid_at": datetime.now(timezone.utc).isoformat(),
    }
    payments.insert(0, entry)

    contract["saas_payments"] = payments
    contract["total_saas_collected_paise"] = sum(p["amount_paid_paise"] for p in payments)
    contract["saas_payment_status"] = (
        "PAID" if contract["total_saas_collected_paise"] >= (tenant.base_license_fee_paise or 0) else "PARTIAL"
    )

    tenant.commercial_contract_json = json.dumps(contract)
    control_db.add(
        ControlPlaneAuditLog(
            action="RECORD_SAAS_PAYMENT",
            target_slug=tenant.subdomain_slug,
            details=f"Collected INR {payload.amount_paid_paise // 100} via {payload.payment_method} (Ref: {payload.transaction_reference})",
        )
    )
    control_db.commit()
    return {
        "status": "success",
        "subdomain_slug": tenant.subdomain_slug,
        "receipt": entry,
        "total_saas_collected_paise": contract["total_saas_collected_paise"],
        "saas_payment_status": contract["saas_payment_status"],
    }
