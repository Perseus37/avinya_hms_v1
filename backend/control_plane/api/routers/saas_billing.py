import stripe
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from backend.core.config import settings
from backend.core.database import get_control_db
from backend.control_plane.models import TenantRegistry

router = APIRouter(prefix="/api/saas", tags=["Control Plane Billing"])

stripe.api_key = settings.STRIPE_SECRET_KEY
WEBHOOK_SECRET = settings.STRIPE_WEBHOOK_SECRET

@router.post("/webhook/stripe")
async def stripe_subscription_webhook(
    request: Request,
    stripe_signature: str = Header(None),
    control_db: Session = Depends(get_control_db)
):
    """Listens to Stripe to auto-suspend or renew hospital tenant access."""
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Stripe billing integration is not configured.",
        )
    payload = await request.body()

    try:
        # Offload CPU-bound cryptography to prevent event loop blocking
        event = await run_in_threadpool(
            stripe.Webhook.construct_event, payload, stripe_signature, WEBHOOK_SECRET
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if event["type"] == "invoice.payment_succeeded":
        customer_id = event["data"]["object"]["customer"]

        tenant = control_db.query(TenantRegistry).filter(
            TenantRegistry.stripe_customer_id == customer_id
        ).first()

        if tenant:
            tenant.is_active = True
            tenant.license_valid_till = datetime.fromtimestamp(
                event["data"]["object"]["lines"]["data"][0]["period"]["end"],
                tz=timezone.utc
            )
            control_db.commit()

    elif event["type"] in ("invoice.payment_failed", "customer.subscription.deleted"):
        customer_id = event["data"]["object"]["customer"]
        tenant = control_db.query(TenantRegistry).filter(
            TenantRegistry.stripe_customer_id == customer_id
        ).first()

        if tenant:
            tenant.is_active = False
            tenant.maintenance_notice = "Platform access suspended due to payment failure. Please update your billing details."
            control_db.commit()

    return {"status": "success"}