from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import settings
from backend.control_plane.models import (
    BillingCycleEnum,
    ControlPlaneAuditLog,
    DeploymentModeEnum,
    FederatedNode,
    PricingModelEnum,
    TenantRegistry,
)
from backend.control_plane.services.blueprints import BLUEPRINTS, apply_hospital_blueprint
from backend.control_plane.services.auth import _verify_vendor_key, verify_control_plane_secret
from backend.control_plane.services.pricing import get_bundle_catalog, get_module_catalog, compute_contract_fee_paise
from backend.control_plane.services.pricing import normalize_contract_arr_paise
from backend.core.mailer import MailDeliveryError, send_email
from backend.core.database import ensure_postgres_db_exists, get_control_db
from backend.domains.identity.models import User
from backend.domains.policies.models import FederationPolicyEnum, HospitalPolicySetting
from backend.domains.federation.models import FederatedClient
from backend.security.envelope import encrypt_tenant_secret
from backend.core.seed import seed_universal_hospital_data
from backend.scripts.migrate import upgrade_tenant_database_url

logger = logging.getLogger("avinya.provisioning")
router = APIRouter(prefix="/api", tags=["Control Plane Onboarding"])

class OnboardHospitalRequest(BaseModel):
    master_setup_key: str
    hospital_name: str = Field(min_length=2, max_length=150)
    subdomain_slug: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9\-]+$")
    branch_code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9\-_]+$")
    admin_email: EmailStr
    pricing_model: str = "BUNDLE_TOTAL_HOSPITAL"
    billing_cycle: str = "ANNUAL_LICENSE"
    selected_modules: List[str] = Field(default_factory=list)
    base_license_fee_paise: Optional[int] = None
    quota_max_beds: int = 500
    quota_max_users: int = 200
    blueprint_archetype: str = "TIER_1_QUATERNARY_500_BED"
    federation_sharing_mode: FederationPolicyEnum = FederationPolicyEnum.FULL_MESH
    brand_primary_color: str = "#06b6d4"
    seed_icd10: bool = True
    seed_allergies: bool = True
    seed_complaints: bool = True

    @field_validator("pricing_model")
    @classmethod
    def validate_pricing_model(cls, value: str) -> str:
        try:
            return PricingModelEnum(value).value
        except ValueError as exc:
            raise ValueError("Unsupported pricing model.") from exc

    @field_validator("billing_cycle")
    @classmethod
    def validate_billing_cycle(cls, value: str) -> str:
        try:
            return BillingCycleEnum(value).value
        except ValueError as exc:
            raise ValueError("Unsupported billing cycle.") from exc

    @field_validator("blueprint_archetype")
    @classmethod
    def validate_blueprint(cls, value: str) -> str:
        if value not in BLUEPRINTS:
            raise ValueError("Unsupported hospital blueprint.")
        return value

    @field_validator("subdomain_slug", mode="before")
    @classmethod
    def normalize_subdomain_slug(cls, v: str) -> str:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("branch_code", mode="before")
    @classmethod
    def normalize_branch_code(cls, v: str) -> str:
        return v.strip().upper() if isinstance(v, str) else v

def _dispatch_welcome_email(to_email: str, data: dict) -> tuple[bool, Optional[str]]:
    try:
        send_email(
            to_email,
            f"Avinya HMS provisioned: {data['hospital_name']}",
            (
            f"Hospital Name : {data['hospital_name']}\n"
            f"Branch Code   : {data['branch_code']}\n"
            f"Pricing Model : {data['pricing_model']} ({data['billing_cycle']})\n"
            f"Access URL    : {data['subdomain_url']}\n\n"
            f"Admin Email   : {data['admin_email']}\n\n"
            "For security, initial credentials and recovery keys are not sent "
            "by email. Retrieve them from the authorized provisioning console."
            ),
        )
        return True, None
    except MailDeliveryError as exc:
        logger.warning("Provisioning welcome email could not be delivered: %s", str(exc))
        return False, str(exc)

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
    target_db_name = f"{settings.TENANT_DATABASE_PREFIX}{db_slug}"

    existing = control_db.execute(
        select(TenantRegistry).where(
            (TenantRegistry.subdomain_slug == slug) | (TenantRegistry.branch_code == branch_code)
        )
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(status_code=409, detail="Subdomain or Branch Code is already registered.")

    dynamic_modules = get_module_catalog(control_db)
    dynamic_bundles = get_bundle_catalog(control_db)

    bundle_obj = dynamic_bundles.get(req.pricing_model, dynamic_bundles.get("CUSTOM_A_LA_CARTE", {"modules": []}))

    is_all = "all" in req.selected_modules
    active_codes = bundle_obj["modules"] if req.pricing_model != "CUSTOM_A_LA_CARTE" else (list(dynamic_modules.keys()) if is_all else req.selected_modules)

    entitlements_map = {m: (m in active_codes) for m in dynamic_modules.keys()}
    if is_all: entitlements_map["all"] = True

    effective_max_beds = req.quota_max_beds if entitlements_map.get("ipd_adt", True) else 0

    contract_fee_paise = compute_contract_fee_paise(
        db=control_db,
        pricing_model=req.pricing_model,
        enabled_modules=entitlements_map,
        custom_fee_paise=req.base_license_fee_paise,
        billing_cycle=req.billing_cycle,
        quota_max_beds=effective_max_beds
    )

    norm_arr = normalize_contract_arr_paise(contract_fee_paise, req.billing_cycle)

    quote = {
        "billing_period_fee_paise": contract_fee_paise,
        "normalized_annual_arr_paise": norm_arr
    }
    contract_fee_paise = quote["billing_period_fee_paise"]

    jwt_secret = secrets.token_hex(32)
    backup_key = secrets.token_urlsafe(32)
    admin_password = f"Hms!{secrets.token_urlsafe(12)}"
    raw_fed_key = f"fed_live_{secrets.token_urlsafe(32)}"
    fed_key_hash = hashlib.sha256(raw_fed_key.encode("utf-8")).hexdigest()

    try:
        target_db_url = ensure_postgres_db_exists(target_db_name)
        upgrade_tenant_database_url(target_db_url)
    except Exception as exc:
        logger.exception("Tenant database creation or migration failed for %s.", slug)
        control_db.add(
            ControlPlaneAuditLog(
                action="PROVISION_FAILED",
                target_slug=slug,
                details="Tenant database creation or schema migration failed.",
            )
        )
        control_db.commit()
        raise HTTPException(
            status_code=500,
            detail="Tenant database creation or schema migration failed; the failed step was recorded.",
        ) from exc

    t_engine = create_engine(target_db_url)
    TSession = sessionmaker(bind=t_engine, autocommit=False, autoflush=False)
    tenant_db = None

    try:
        tenant_db = TSession()
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
        policy = tenant_db.execute(select(HospitalPolicySetting)).scalars().first()
        if not policy:
            policy = HospitalPolicySetting(federation_sharing_mode=req.federation_sharing_mode)
            tenant_db.add(policy)
        else:
            policy.federation_sharing_mode = req.federation_sharing_mode
        admin_user = tenant_db.query(User).filter(User.email == req.admin_email).first()
        admin_id = admin_user.id if admin_user else 1

        blueprint_summary = apply_hospital_blueprint(tenant_db, req.blueprint_archetype, admin_id)
        tenant_db.commit()
    except Exception:
        if tenant_db:
            tenant_db.rollback()
        logger.exception("Tenant initialization or blueprint setup failed for %s.", slug)
        control_db.add(
            ControlPlaneAuditLog(
                action="PROVISION_FAILED",
                target_slug=slug,
                details="Tenant initialization or blueprint setup failed.",
            )
        )
        control_db.commit()
        raise HTTPException(status_code=500, detail="Failed to initialize tenant schemas and blueprints.")
    finally:
        if tenant_db:
            tenant_db.close()
        t_engine.dispose()

    subdomain_url = settings.tenant_url(slug)
    contract_meta = {**quote, "provisioned_at": datetime.now(timezone.utc).isoformat()}
    quotas_dict = {"max_beds": effective_max_beds, "max_users": req.quota_max_users}
    branding_dict = {"primary_color": req.brand_primary_color}
    provisioning_config = {
        "blueprint_archetype": req.blueprint_archetype,
        "seed_icd10": req.seed_icd10,
        "seed_allergies": req.seed_allergies,
        "seed_complaints": req.seed_complaints,
    }

    tenant_entry = TenantRegistry(
        hospital_name=req.hospital_name,
        branch_code=branch_code,
        subdomain_slug=slug,
        db_name=target_db_name,
        admin_email=req.admin_email,
        jwt_secret_key=encrypt_tenant_secret(jwt_secret),
        backup_encryption_key=encrypt_tenant_secret(backup_key),
        deployment_mode=DeploymentModeEnum.CLOUD,
        pricing_model_string=req.pricing_model,
        billing_cycle_string=req.billing_cycle,
        base_license_fee_paise=contract_fee_paise,
        enabled_modules_json=json.dumps(entitlements_map),
        commercial_contract_json=json.dumps(contract_meta),
        quota_limits_json=json.dumps(quotas_dict),
        ui_branding_json=json.dumps(branding_dict),
        provisioning_config_json=json.dumps(provisioning_config),
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
            details=f"Model: {req.pricing_model} ({req.billing_cycle}), Fee: INR {contract_fee_paise // 100}, ARR: INR {quote['normalized_annual_arr_paise'] // 100}, Blueprint: {req.blueprint_archetype}"
        ),
    ])
    control_db.commit()

    result = {
        "hospital_name": req.hospital_name,
        "branch_code": branch_code,
        "subdomain_slug": slug,
        "subdomain_url": subdomain_url,
        "deployment_mode": DeploymentModeEnum.CLOUD.value,
        "pricing_model": req.pricing_model,
        "billing_cycle": req.billing_cycle,
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
        "provisioning_config": provisioning_config,
        "spatial_summary": blueprint_summary,
        "database_name": target_db_name,
        "admin_email": req.admin_email,
        "admin_password": admin_password,
        "jwt_secret_key": jwt_secret,
        "federation_api_key": raw_fed_key,
        "backup_encryption_key": backup_key,
    }
    email_dispatched, email_error = _dispatch_welcome_email(req.admin_email, result)
    result["email_dispatched"] = email_dispatched
    result["email_error"] = email_error
    if not email_dispatched:
        control_db.add(
            ControlPlaneAuditLog(
                action="PROVISION_EMAIL_FAILED",
                target_slug=slug,
                details=email_error or "Welcome email delivery failed.",
            )
        )
        control_db.commit()
    return result