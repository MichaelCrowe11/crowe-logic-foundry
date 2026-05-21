"""
Stripe billing integration for Crowe Logic Foundry Control Plane.

Handles subscription lifecycle, checkout sessions, customer portal,
and usage-based billing metering. All Stripe operations go through
this module so the rest of the control plane stays billing-agnostic.
"""

import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from .db import Database, get_db
from .plans import LAUNCH_PLAN_IDS, canonical_plan_id, stripe_price_id

router = APIRouter(prefix="/api/billing", tags=["billing"])

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")

# Plan ID to Stripe Price ID mapping. Keys match the tier_key values in
# config/customer_pricing.json so webhooks and checkout flows use one
# consistent vocabulary. Set the STRIPE_PRICE_* env vars in the Railway
# environment to the actual price IDs after running the Stripe bootstrap.
#
# Legacy keys (developer/studio/lab) kept as aliases so in-flight
# subscriptions created under the old scheme continue to resolve.
PLAN_PRICE_MAP = {
    "personal":   os.environ.get("STRIPE_PRICE_PERSONAL", ""),
    "pro":        os.environ.get("STRIPE_PRICE_PRO", ""),
    "team":       os.environ.get("STRIPE_PRICE_TEAM", ""),
    "enterprise": os.environ.get("STRIPE_PRICE_ENTERPRISE", ""),
    "byok":       os.environ.get("STRIPE_PRICE_BYOK", ""),
    # Legacy aliases (do not remove until all 2025-era subscriptions migrate)
    "developer":  os.environ.get("STRIPE_PRICE_DEVELOPER", ""),
    "studio":     os.environ.get("STRIPE_PRICE_STUDIO", ""),
    "lab":        os.environ.get("STRIPE_PRICE_LAB", ""),
}

# Usage metering price (per 1K tokens overage)
USAGE_METER_PRICE = os.environ.get("STRIPE_PRICE_USAGE_TOKENS", "")

SUCCESS_URL = os.environ.get("STRIPE_SUCCESS_URL", "https://api.crowelogic.com/billing/success")
CANCEL_URL = os.environ.get("STRIPE_CANCEL_URL", "https://api.crowelogic.com/billing/cancel")
PORTAL_RETURN_URL = os.environ.get("STRIPE_PORTAL_RETURN_URL", "https://api.crowelogic.com/account")


def _get_stripe():
    """Lazy-import stripe SDK so the module loads even without the package."""
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        return stripe
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Stripe SDK not installed. Run: pip install stripe"
        )


# ─── Request / Response Models ──────────────────────────────────────

class CheckoutRequest(BaseModel):
    workspace_id: str
    plan_id: str
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class PortalRequest(BaseModel):
    workspace_id: str
    return_url: Optional[str] = None


class SubscriptionResponse(BaseModel):
    subscription_id: str
    status: str
    plan_id: str
    current_period_start: str
    current_period_end: str


# ─── Helpers ────────────────────────────────────────────────────────

async def _get_or_create_customer(db: Database, workspace_id: str) -> str:
    """Get or create a Stripe customer for the workspace's org."""
    ws = await db.fetchrow("SELECT * FROM workspaces WHERE id = $1", workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    org = await db.fetchrow("SELECT * FROM organizations WHERE id = $1", ws["org_id"])
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if org.get("stripe_customer_id"):
        return org["stripe_customer_id"]

    # Create Stripe customer
    stripe = _get_stripe()
    owner = await db.fetchrow("SELECT * FROM users WHERE id = $1", org["owner_id"])
    customer = stripe.Customer.create(
        email=owner["email"] if owner else None,
        name=org["name"],
        metadata={"org_id": org["id"], "workspace_id": workspace_id},
    )

    await db.execute(
        "UPDATE organizations SET stripe_customer_id = $1 WHERE id = $2",
        customer.id, org["id"],
    )
    return customer.id


# ─── Endpoints ──────────────────────────────────────────────────────

@router.post("/checkout")
async def create_checkout_session(
    req: CheckoutRequest,
    db: Database = Depends(get_db),
):
    """Create a Stripe Checkout session for plan subscription."""
    stripe = _get_stripe()
    plan_id = canonical_plan_id(req.plan_id)
    price_id = stripe_price_id(plan_id) or PLAN_PRICE_MAP.get(req.plan_id)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"No Stripe price configured for plan '{plan_id}'")

    customer_id = await _get_or_create_customer(db, req.workspace_id)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=req.success_url or SUCCESS_URL,
        cancel_url=req.cancel_url or CANCEL_URL,
        metadata={
            "workspace_id": req.workspace_id,
            "plan_id": plan_id,
        },
        subscription_data={
            "metadata": {
                "workspace_id": req.workspace_id,
                "plan_id": plan_id,
            },
        },
    )

    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/portal")
async def create_portal_session(
    req: PortalRequest,
    db: Database = Depends(get_db),
):
    """Create a Stripe Customer Portal session for self-service management."""
    stripe = _get_stripe()
    customer_id = await _get_or_create_customer(db, req.workspace_id)

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=req.return_url or PORTAL_RETURN_URL,
    )

    return {"portal_url": session.url}


@router.get("/subscription/{workspace_id}")
async def get_subscription(
    workspace_id: str,
    db: Database = Depends(get_db),
):
    """Get subscription status for a workspace."""
    sub = await db.fetchrow(
        "SELECT * FROM subscriptions WHERE workspace_id = $1", workspace_id
    )
    if not sub:
        return {"status": "none", "workspace_id": workspace_id}

    return SubscriptionResponse(
        subscription_id=sub["id"],
        status=sub["status"],
        plan_id=sub["plan_id"],
        current_period_start=str(sub["current_period_start"]),
        current_period_end=str(sub["current_period_end"]),
    )


@router.post("/usage/report")
async def report_usage(
    db: Database = Depends(get_db),
):
    """Report aggregated token usage to Stripe for billing.

    Called by a scheduled job — aggregates unreported usage_events
    and creates Stripe usage records for metered billing.
    """
    stripe = _get_stripe()

    if not USAGE_METER_PRICE:
        return {"status": "skipped", "reason": "No usage meter price configured"}

    # Find workspaces with unreported usage above their plan budget
    rows = await db.fetch("""
        SELECT ue.workspace_id, w.plan_id, w.stripe_subscription_id,
               SUM(ue.quantity) AS total_tokens,
               p.token_budget_month
        FROM usage_events ue
        JOIN workspaces w ON ue.workspace_id = w.id
        JOIN plans p ON w.plan_id = p.id
        WHERE ue.event_type = 'tokens'
          AND ue.metadata NOT LIKE '%reported_to_stripe%'
        GROUP BY ue.workspace_id
    """)

    reported = 0
    for row in rows:
        budget = row["token_budget_month"]
        total = row["total_tokens"]
        if budget == -1 or total <= budget:
            continue  # within budget, no overage

        overage = total - budget
        overage_units = max(1, overage // 1000)  # bill per 1K tokens

        sub_id = row.get("stripe_subscription_id")
        if not sub_id:
            continue

        try:
            # Find the subscription item for the metered price
            sub = stripe.Subscription.retrieve(sub_id)
            meter_item = None
            for item in sub["items"]["data"]:
                if item["price"]["id"] == USAGE_METER_PRICE:
                    meter_item = item
                    break

            if meter_item:
                stripe.SubscriptionItem.create_usage_record(
                    meter_item.id,
                    quantity=overage_units,
                    timestamp=int(time.time()),
                    action="increment",
                )
                reported += 1
        except Exception as e:
            print(f"[billing] Usage report error for {row['workspace_id']}: {e}")

    return {"status": "ok", "workspaces_reported": reported}


# NOTE: the Stripe webhook used to live here at /api/billing/webhook
# but FastAPI route resolution preferred the app-level handler in
# control_plane/__init__.py, so this version was dead code with stale
# logic. The live webhook is now the single source of truth in
# control_plane/__init__.py:stripe_webhook, with idempotent event
# processing and per-event-type helpers covered by tests in
# tests/test_billing_webhook.py.


@router.get("/config")
async def billing_config():
    """Return public billing configuration for the frontend."""
    return {
        "publishable_key": STRIPE_PUBLISHABLE_KEY,
        "plans": {
            plan_id: {
                "price_id": stripe_price_id(plan_id),
                "configured": bool(stripe_price_id(plan_id)),
            }
            for plan_id in LAUNCH_PLAN_IDS
        },
        "usage_metering": bool(USAGE_METER_PRICE),
    }
