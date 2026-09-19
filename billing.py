# ============================================================
# BILLING — Lemon Squeezy checkout for premium, web-client only.
# The Android app pays through Google Play Billing (see premium.py);
# the web has no app-store equivalent. Stripe doesn't support Rwanda
# as a merchant country, and Flutterwave requires business
# registration that isn't available -- Lemon Squeezy is a Merchant
# of Record (it, not us, is the legal seller on every sale), which
# is what lets an individual sell here without registering a
# business, and it confirmed Rwanda for payouts.
#
# Both paths land in the same `subscriptions` table read by
# premium.is_premium_active -- this only ever WRITES rows tagged
# source="lemonsqueezy", so that function's Play re-verification
# branch never touches them.
#
# Lemon Squeezy signs every webhook with a real HMAC-SHA256 over the
# raw request body (unlike Flutterwave's static verif-hash, which
# this project tried and removed) -- once that signature checks out,
# the payload's own fields are trusted directly, no extra
# re-verification round-trip needed.
#
# SETUP REQUIRED before this works:
#   1. Create a Lemon Squeezy account and a Store.
#   2. Create one Product ("Ollie Premium") with two Variants
#      (monthly, yearly recurring prices) -- note each variant's id.
#   3. Set env vars LEMONSQUEEZY_API_KEY (Settings > API),
#      LEMONSQUEEZY_STORE_ID, LEMONSQUEEZY_VARIANT_MONTHLY,
#      LEMONSQUEEZY_VARIANT_YEARLY, and WEB_APP_URL.
#   4. In the dashboard (Settings > Webhooks), add a webhook pointing
#      at <this API's base url>/billing/webhook, subscribed to at
#      least subscription_created, subscription_updated,
#      subscription_payment_success, and subscription_expired. Set
#      LEMONSQUEEZY_WEBHOOK_SECRET to the signing secret you choose.
# ============================================================

import hashlib
import hmac
import logging
from datetime import datetime

import requests
from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user
from config import (
    LEMONSQUEEZY_API_KEY,
    LEMONSQUEEZY_STORE_ID,
    LEMONSQUEEZY_VARIANT_MONTHLY,
    LEMONSQUEEZY_VARIANT_YEARLY,
    LEMONSQUEEZY_WEBHOOK_SECRET,
    WEB_APP_URL,
)
from database import supabase

logger = logging.getLogger("ollie.billing")
router = APIRouter()

API_BASE = "https://api.lemonsqueezy.com/v1"
REQUEST_TIMEOUT_SECONDS = 15
JSONAPI_HEADERS = {"Content-Type": "application/vnd.api+json", "Accept": "application/vnd.api+json"}

# All subscription lifecycle events carry the same shape (data.attributes.status
# is the current source of truth) -- one handler covers first purchase,
# every renewal, cancellation, and expiry alike, rather than special-casing
# each event name.
SUBSCRIPTION_EVENTS = {
    "subscription_created",
    "subscription_updated",
    "subscription_payment_success",
    "subscription_payment_recovered",
    "subscription_resumed",
    "subscription_unpaused",
    "subscription_cancelled",
    "subscription_paused",
    "subscription_expired",
}


def _variant_id_for(plan):
    return {"monthly": LEMONSQUEEZY_VARIANT_MONTHLY, "yearly": LEMONSQUEEZY_VARIANT_YEARLY}.get(plan)


def _product_id_for(plan):
    # Mirrors the Play product ids in purchase_service.dart/config.py,
    # just tagged "_web" -- premium.py's is_premium_active never
    # inspects product_id itself (source is what it branches on), so
    # this is only ever surfaced back to the client for display.
    return f"ollie_premium_{plan}_web"


@router.post("/create-checkout-session")
def create_checkout_session(data: dict, current_user: dict = Depends(get_current_user)):
    if not LEMONSQUEEZY_API_KEY or not LEMONSQUEEZY_STORE_ID:
        raise HTTPException(status_code=500, detail="Lemon Squeezy is not configured")

    plan = data.get("plan")
    variant_id = _variant_id_for(plan)
    if not variant_id:
        raise HTTPException(status_code=400, detail="plan must be 'monthly' or 'yearly'")

    try:
        response = requests.post(
            f"{API_BASE}/checkouts",
            headers={"Authorization": f"Bearer {LEMONSQUEEZY_API_KEY}", **JSONAPI_HEADERS},
            json={
                "data": {
                    "type": "checkouts",
                    "attributes": {
                        # Echoed back in every related webhook's
                        # meta.custom_data -- this is what ties a
                        # purchase back to our own user id.
                        "checkout_data": {"custom": {"user_id": current_user["id"], "plan": plan}},
                        "product_options": {"redirect_url": f"{WEB_APP_URL}/premium/success"},
                    },
                    "relationships": {
                        "store": {"data": {"type": "stores", "id": str(LEMONSQUEEZY_STORE_ID)}},
                        "variant": {"data": {"type": "variants", "id": str(variant_id)}},
                    },
                },
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        body = response.json()
    except Exception as e:
        logger.error(f"Lemon Squeezy checkout creation failed for user {current_user['id']}: {e}")
        raise HTTPException(status_code=502, detail="Could not start checkout")

    if response.status_code >= 300:
        logger.error(f"Lemon Squeezy checkout creation rejected for user {current_user['id']}: {body}")
        raise HTTPException(status_code=502, detail="Could not start checkout")

    return {"checkout_url": body["data"]["attributes"]["url"]}


@router.post("/webhook")
async def lemonsqueezy_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("x-signature", "")
    if not LEMONSQUEEZY_WEBHOOK_SECRET or not _valid_signature(raw_body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = await request.json()
    event_name = payload.get("meta", {}).get("event_name")
    if event_name in SUBSCRIPTION_EVENTS:
        _sync_subscription(payload)

    return {"received": True}


def _valid_signature(raw_body: bytes, signature: str) -> bool:
    if not signature:
        return False
    digest = hmac.new(LEMONSQUEEZY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def _status_and_expiry_field(attributes: dict):
    status = attributes.get("status")
    if status in ("active", "on_trial"):
        return "active", "renews_at"
    if status == "cancelled":
        # Cancelled keeps paid access through its already-paid-for
        # period -- ends_at is that defined end date, not "now".
        return "active", "ends_at"
    return "expired", None


def _parse_iso_to_ms(iso_str) -> int:
    if not iso_str:
        return 0
    return int(datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp() * 1000)


def _sync_subscription(payload: dict):
    custom_data = payload.get("meta", {}).get("custom_data") or {}
    user_id = custom_data.get("user_id")
    if not user_id:
        logger.warning("Lemon Squeezy webhook with no custom_data.user_id, ignoring")
        return

    attributes = payload.get("data", {}).get("attributes", {})
    status, expiry_field = _status_and_expiry_field(attributes)
    expiry_ms = _parse_iso_to_ms(attributes.get(expiry_field)) if expiry_field else 0

    sub_data = {
        "user_id": user_id,
        "status": status,
        "purchase_token": str(payload.get("data", {}).get("id", "")),
        "product_id": _product_id_for(custom_data.get("plan") or "premium"),
        "expiry_time_millis": expiry_ms,
        "source": "lemonsqueezy",
    }

    existing = supabase.table("subscriptions").select("id").eq("user_id", user_id).execute()
    if existing.data:
        supabase.table("subscriptions").update(sub_data).eq("id", existing.data[0]["id"]).execute()
    else:
        supabase.table("subscriptions").insert(sub_data).execute()
