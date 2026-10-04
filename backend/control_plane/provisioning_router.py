from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import json
import logging
import os
import re
import secrets
import smtplib
from typing import Any, Dict, List, Optional
from alembic import command
from alembic.config import Config
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from config import settings
from control_models import (
    BillingCycleEnum,
    ControlPlaneAuditLog,
    DeploymentModeEnum,
    FederatedNode,
    PricingModelEnum,
    TenantRegistry,
)
from control_plane.blueprints import BLUEPRINTS, apply_hospital_blueprint
from control_plane.fleet_ops_router import _verify_vendor_key, verify_control_plane_secret
from control_plane.pricing import (
    COMMERCIAL_BUNDLES,
    MODULE_CATALOG,
    compute_contract_quote,
    resolve_licensed_modules,
)
from database import ensure_postgres_db_exists, get_control_db
from models import FederatedClient, FederationPolicyEnum, HospitalPolicySetting, User
from security.envelope import encrypt_tenant_secret
from seed import seed_universal_hospital_data

logger = logging.getLogger("avinya.provisioning")
router = APIRouter(prefix="/api", tags=["Control Plane Onboarding"])


class OnboardHospitalRequest(BaseModel):
    master_setup_key: str = Field(min_length=8)
    hospital_name: str = Field(min_length=2, max_length=150)
    subdomain_slug: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9\-]+$")
    branch_code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9\-_]+$")
    admin_email: EmailStr
    deployment_mode: DeploymentModeEnum = DeploymentModeEnum.CLOUD

    # Modular & Bundle Commercial Fields
    pricing_model: PricingModelEnum = PricingModelEnum.BUNDLE_TOTAL_HOSPITAL
    subscription_tier: Optional[PricingModelEnum] = None
    billing_cycle: BillingCycleEnum = BillingCycleEnum.ANNUAL_LICENSE
    selected_modules: Optional[List[str]] = Field(default_factory=list)
    base_license_fee_paise: Optional[int] = None
    quota_max_beds: int = 500
    quota_max_users: int = 200

    # Day-0 Governance, Sovereignty & Branding
    blueprint_archetype: str = "TIER_1_QUATERNARY_500_BED"
    federation_sharing_mode: FederationPolicyEnum = FederationPolicyEnum.FULL_MESH
    brand_primary_color: str = "#06b6d4"
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


def _dispatch_welcome_email(to_email: str, data: dict) -> bool:
    if not settings.SMTP_HOST:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = f"Avinya HMS Provisioned: {data['hospital_name']} ({data['subdomain_url']})"
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to_email
        msg.set_content(
            f"Hospital Name : {data['hospital_name']}\n"
            f"Branch Code   : {data['branch_code']}\n"
            f"Pricing Model : {data['pricing_model']} ({data['billing_cycle']})\n"
            f"Access URL    : {data['subdomain_url']}\n\n"
            f"Admin Email            : {data['admin_email']}\n"
            f"Admin One-Time Password: {data['admin_password']}\n"
            f"Federation API Key     : {data['federation_api_key']}\n"
            f"Backup Recovery Key    : {data['backup_encryption_key']}\n"
        )
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=5) as server:
            if settings.SMTP_USER:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
            server.send_message(msg)
        return True
    except Exception as exc:
        logger.warning("Email dispatch skipped/failed: %s", str(exc))
        return False


@router.get("/blueprints")
def list_available_hospital_blueprints() -> Dict[str, Any]:
    """Exposes pre-built structural and clinical archetypes."""
    return {
        "blueprints": [
            {
                "key": k,
                "name": v["name"],
                "description": v["description"],
                "floors_count": len(v["floors"]),
                "theatres_count": len(v.get("theatres", [])),
            }
            for k, v in BLUEPRINTS.items()
        ]
    }


@router.get("/pricing/catalog")
def get_commercial_pricing_catalog() -> Dict[str, Any]:
    """Exposes all à la carte modules and bundle pricing for the onboarding wizard."""
    return {
        "modules": list(MODULE_CATALOG.values()),
        "bundles": [{"code": k, **v} for k, v in COMMERCIAL_BUNDLES.items()],
    }


@router.post("/provision")
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

    existing = control_db.execute(
        select(TenantRegistry).where(
            (TenantRegistry.subdomain_slug == slug) | (TenantRegistry.branch_code == branch_code)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Subdomain or Branch Code is already registered.")

    effective_pricing_model = req.subscription_tier or req.pricing_model
    entitlements_map = resolve_licensed_modules(effective_pricing_model.value, req.selected_modules)

    # Auto-zero bed quota if IPD module is not licensed
    effective_max_beds = req.quota_max_beds if entitlements_map.get("ipd_adt", True) else 0

    quote = compute_contract_quote(
        pricing_model=effective_pricing_model.value,
        enabled_modules=entitlements_map,
        billing_cycle=req.billing_cycle.value,
        quota_max_beds=effective_max_beds,
        custom_fee_paise=req.base_license_fee_paise,
    )
    contract_fee_paise = quote["billing_period_fee_paise"]

    jwt_secret = secrets.token_hex(32)
    backup_key = secrets.token_urlsafe(32)
    admin_password = f"Hms!{secrets.token_urlsafe(12)}"
    raw_fed_key = f"fed_live_{secrets.token_urlsafe(32)}"
    fed_key_hash = hashlib.sha256(raw_fed_key.encode("utf-8")).hexdigest()

    target_db_url = ensure_postgres_db_exists(target_db_name)
    os.environ["DATABASE_URL"] = target_db_url
    alembic_ini_path = "backend/alembic.ini" if os.path.exists("backend/alembic.ini") else "alembic.ini"
    alembic_cfg = Config(alembic_ini_path)
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
        # Seed Day-0 Hospital Policy & Federation Sovereignty
        policy = tenant_db.execute(select(HospitalPolicySetting)).scalars().first()
        if not policy:
            policy = HospitalPolicySetting(federation_sharing_mode=req.federation_sharing_mode)
            tenant_db.add(policy)
        else:
            policy.federation_sharing_mode = req.federation_sharing_mode

        admin_user = tenant_db.query(User).filter(User.email == req.admin_email).first()
        admin_id = admin_user.id if admin_user else 1
        blueprint_summary = apply_hospital_blueprint(tenant_db, req.blueprint_archetype, admin_id)
    t_engine.dispose()

    subdomain_url = f"https://{slug}.{settings.BASE_DOMAIN}"
    contract_meta = {
        **quote,
        "provisioned_at": datetime.now(timezone.utc).isoformat(),
    }
    quotas_dict = {"max_beds": effective_max_beds, "max_users": req.quota_max_users}
    branding_dict = {"primary_color": req.brand_primary_color}

    tenant_entry = TenantRegistry(
        hospital_name=req.hospital_name,
        branch_code=branch_code,
        subdomain_slug=slug,
        db_name=target_db_name,
        admin_email=req.admin_email,
        jwt_secret_key=encrypt_tenant_secret(jwt_secret),
        backup_encryption_key=encrypt_tenant_secret(backup_key),
        deployment_mode=DeploymentModeEnum.CLOUD,
        pricing_model=effective_pricing_model,
        billing_cycle=req.billing_cycle,
        base_license_fee_paise=contract_fee_paise,
        enabled_modules_json=json.dumps(entitlements_map),
        commercial_contract_json=json.dumps(contract_meta),
        quota_limits_json=json.dumps(quotas_dict),
        ui_branding_json=json.dumps(branding_dict),
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
    control_db.add_all([
        tenant_entry,
        fed_node,
        ControlPlaneAuditLog(
            action="PROVISION_HOSPITAL",
            target_slug=slug,
            details=(
                f"Model: {effective_pricing_model.value} ({req.billing_cycle.value}), "
                f"Fee: INR {contract_fee_paise // 100}, ARR: INR {quote['normalized_annual_arr_paise'] // 100}, "
                f"Blueprint: {req.blueprint_archetype}, Sovereignty: {req.federation_sharing_mode.value}"
            ),
        ),
    ])
    control_db.commit()

    result = {
        "hospital_name": req.hospital_name,
        "branch_code": branch_code,
        "subdomain_slug": slug,
        "subdomain_url": subdomain_url,
        "deployment_mode": DeploymentModeEnum.CLOUD.value,
        "pricing_model": effective_pricing_model.value,
        "subscription_tier": effective_pricing_model.value,
        "billing_cycle": req.billing_cycle.value,
        "base_license_fee_paise": contract_fee_paise,
        "commercial_quote": quote,
        "enabled_modules": entitlements_map,
        "quota_limits": quotas_dict,
        "quota_utilization": {
            "beds_provisioned": f"{blueprint_summary['beds']} / {effective_max_beds}",
            "users_provisioned": f"1 / {req.quota_max_users}",
        },
        "federation_sharing_mode": req.federation_sharing_mode.value,
        "blueprint_archetype": req.blueprint_archetype,
        "spatial_summary": blueprint_summary,
        "database_name": target_db_name,
        "admin_email": req.admin_email,
        "admin_password": admin_password,
        "jwt_secret_key": jwt_secret,
        "federation_api_key": raw_fed_key,
        "backup_encryption_key": backup_key,
    }
    result["email_dispatched"] = _dispatch_welcome_email(req.admin_email, result)
    return result