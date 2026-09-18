# ============================================================
# BILLING — Stripe checkout for premium, web-client only. The
# Android app pays through Google Play Billing (see premium.py);
# the web has no app-store equivalent, so it buys a subscription
# through Stripe Checkout instead. Both paths land in the same
# `subscriptions` table and are read by the same
# premium.is_premium_active — this only ever WRITES rows tagged
# source="stripe", so that function's Play re-verification branch
# never touches them.
#
# SETUP REQUIRED before this works:
#   1. Create a Stripe account, then in the Dashboard create two
#      recurring Prices (monthly, yearly) under a "Ollie Premium"
#      product.
#   2. Set env vars STRIPE_SECRET_KEY, STRIPE_PRICE_MONTHLY,
#      STRIPE_PRICE_YEARLY, and WEB_APP_URL (the deployed web app's
#      origin, e.g. https://ollie-web.vercel.app).
#   3. In the Stripe Dashboard, add a webhook endpoint pointing at
#      <this API's base url>/billing/webhook, subscribed to
#      checkout.session.completed, customer.subscription.updated,
#      and customer.subscription.deleted. Set STRIPE_WEBHOOK_SECRET
#      to the signing secret it gives you.
# ============================================================

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user
from config import (
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    STRIPE_PRICE_MONTHLY,
    STRIPE_PRICE_YEARLY,
    WEB_APP_URL,
)
from database import supabase

logger = logging.getLogger("ollie.billing")
router = APIRouter()
stripe.api_key = STRIPE_SECRET_KEY

# These two build their dict fresh on every call rather than once
# at import time -- STRIPE_PRICE_MONTHLY/YEARLY are read from env at
# process startup (see config.py), so a dict frozen at import time
# would be fine in production, but the two are easy to confuse and
# freezing them cost nothing to avoid.

def _price_id_for_plan(plan):
    return {"monthly": STRIPE_PRICE_MONTHLY, "yearly": STRIPE_PRICE_YEARLY}.get(plan)


def _product_id_for_price(price_id):
    # Mirrors the Play product ids in purchase_service.dart/config.py,
    # just tagged "_web" -- premium.py's is_premium_active never
    # inspects product_id itself (source is what it branches on), so
    # this is only ever surfaced back to the client for display.
    return {
        STRIPE_PRICE_MONTHLY: "ollie_premium_monthly_web",
        STRIPE_PRICE_YEARLY: "ollie_premium_yearly_web",
    }.get(price_id, price_id)


@router.post("/create-checkout-session")
def create_checkout_session(data: dict, current_user: dict = Depends(get_current_user)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe is not configured")

    price_id = _price_id_for_plan(data.get("plan"))
    if not price_id:
        raise HTTPException(status_code=400, detail="plan must be 'monthly' or 'yearly'")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            # Lets the webhook (which only ever sees Stripe's own ids)
            # tie the completed checkout back to our user.
            client_reference_id=current_user["id"],
            success_url=f"{WEB_APP_URL}/premium/success",
            cancel_url=f"{WEB_APP_URL}/premium",
        )
    except Exception as e:
        logger.error(f"Stripe checkout session creation failed for user {current_user['id']}: {e}")
        raise HTTPException(status_code=502, detail="Could not start checkout")

    return {"checkout_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logger.warning(f"Stripe webhook signature check failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _activate_from_checkout(obj)
    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        _sync_from_subscription(obj["id"], obj)

    return {"received": True}


def _activate_from_checkout(session: dict):
    user_id = session.get("client_reference_id")
    subscription_id = session.get("subscription")
    if not user_id or not subscription_id:
        # Not a subscription checkout, or missing the reference we
        # need -- nothing we can activate.
        return
    sub = stripe.Subscription.retrieve(subscription_id)
    _upsert_subscription(user_id, sub)


def _sync_from_subscription(subscription_id: str, sub: dict):
    # Renewal/cancellation events carry no client_reference_id (that
    # only exists on the original Checkout Session) -- recover the
    # user from the row this same subscription id activated earlier.
    existing = supabase.table("subscriptions").select("user_id") \
        .eq("purchase_token", subscription_id).execute()
    if not existing.data:
        logger.warning(f"Stripe webhook for unknown subscription {subscription_id}")
        return
    _upsert_subscription(existing.data[0]["user_id"], sub)


def _upsert_subscription(user_id: str, sub):
    status = "active" if sub["status"] in ("active", "trialing") else "expired"
    price_id = sub["items"]["data"][0]["price"]["id"]
    sub_data = {
        "user_id": user_id,
        "status": status,
        "purchase_token": sub["id"],
        "product_id": _product_id_for_price(price_id),
        "expiry_time_millis": int(sub["current_period_end"]) * 1000,
        "source": "stripe",
    }

    existing = supabase.table("subscriptions").select("id").eq("user_id", user_id).execute()
    if existing.data:
        supabase.table("subscriptions").update(sub_data).eq("id", existing.data[0]["id"]).execute()
    else:
        supabase.table("subscriptions").insert(sub_data).execute()
