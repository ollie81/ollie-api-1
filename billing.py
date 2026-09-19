# ============================================================
# BILLING — Flutterwave checkout for premium, web-client only. The
# Android app pays through Google Play Billing (see premium.py); the
# web has no app-store equivalent, and Stripe doesn't support Rwanda
# as a merchant country, so this uses Flutterwave instead (licensed
# by the National Bank of Rwanda, accepts international cards from
# anywhere plus MTN/Airtel Mobile Money locally). Both paths land in
# the same `subscriptions` table read by premium.is_premium_active —
# this only ever WRITES rows tagged source="flutterwave", so that
# function's Play re-verification branch never touches them.
#
# Uses Flutterwave's v3 REST API directly via `requests` rather than
# a third-party SDK wrapper -- there's no official Python SDK, and a
# handful of plain HTTP calls against a documented REST API is easier
# to verify correct than trusting an unofficial one.
#
# Trust model: Flutterwave's webhook signature (the verif-hash header)
# is a static value you configure -- NOT an HMAC over the payload, so
# matching it only proves *a* request came from someone who knows
# your secret hash, not that THIS payload's contents are genuine.
# Flutterwave's own docs say as much: "always re-query our API to
# verify the transaction details" before granting anything. So the
# webhook here only extracts a transaction id and immediately re-fetches
# the authoritative status from Flutterwave's own Verify Transaction
# endpoint -- premium is only ever granted based on that response, and
# the tx_ref/amount are cross-checked there too, not trusted from the
# webhook body.
#
# SETUP REQUIRED before this works:
#   1. Create a Flutterwave account for Rwanda (business registration
#      is required for Rwandan merchants) and get it enabled for
#      international card payments (Dashboard request, ~48h review).
#   2. Create two Payment Plans (Dashboard, or POST
#      /v3/payment-plans) -- monthly and yearly -- and note their
#      numeric plan ids.
#   3. Set env vars FLUTTERWAVE_SECRET_KEY, FLUTTERWAVE_PLAN_MONTHLY,
#      FLUTTERWAVE_PLAN_YEARLY, FLUTTERWAVE_PRICE_MONTHLY,
#      FLUTTERWAVE_PRICE_YEARLY (amounts, matching what each plan was
#      created with), FLUTTERWAVE_CURRENCY (defaults to USD), and
#      WEB_APP_URL (the deployed web app's origin).
#   4. In the Flutterwave Dashboard, under Settings > Webhooks, set
#      a secret hash and point the webhook URL at
#      <this API's base url>/billing/webhook. Set
#      FLUTTERWAVE_WEBHOOK_SECRET_HASH to that same secret hash.
# ============================================================

import logging
import secrets
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user
from config import (
    FLUTTERWAVE_SECRET_KEY,
    FLUTTERWAVE_WEBHOOK_SECRET_HASH,
    FLUTTERWAVE_PLAN_MONTHLY,
    FLUTTERWAVE_PLAN_YEARLY,
    FLUTTERWAVE_PRICE_MONTHLY,
    FLUTTERWAVE_PRICE_YEARLY,
    FLUTTERWAVE_CURRENCY,
    WEB_APP_URL,
)
from database import supabase

logger = logging.getLogger("ollie.billing")
router = APIRouter()

API_BASE = "https://api.flutterwave.com/v3"
REQUEST_TIMEOUT_SECONDS = 15
# tx_ref is entirely self-generated (Flutterwave just requires it to
# be unique), so the user id and plan are encoded directly into it --
# this is what lets the webhook/verify round-trip recover who to
# activate premium for. Supabase user ids are UUIDs (hyphens, no
# underscores), so splitting on "_" is unambiguous.
_TX_REF_PREFIX = "ollie"


def _auth_headers():
    return {"Authorization": f"Bearer {FLUTTERWAVE_SECRET_KEY}"}


def _plan_id_for(plan):
    return {"monthly": FLUTTERWAVE_PLAN_MONTHLY, "yearly": FLUTTERWAVE_PLAN_YEARLY}.get(plan)


def _price_for(plan):
    return {"monthly": FLUTTERWAVE_PRICE_MONTHLY, "yearly": FLUTTERWAVE_PRICE_YEARLY}.get(plan)


def _product_id_for(plan):
    # Mirrors the Play product ids in purchase_service.dart/config.py,
    # just tagged "_web" -- premium.py's is_premium_active never
    # inspects product_id itself (source is what it branches on), so
    # this is only ever surfaced back to the client for display.
    return f"ollie_premium_{plan}_web"


@router.post("/create-checkout-session")
def create_checkout_session(data: dict, current_user: dict = Depends(get_current_user)):
    if not FLUTTERWAVE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Flutterwave is not configured")

    plan = data.get("plan")
    plan_id = _plan_id_for(plan)
    price = _price_for(plan)
    if not plan_id or not price:
        raise HTTPException(status_code=400, detail="plan must be 'monthly' or 'yearly'")

    tx_ref = f"{_TX_REF_PREFIX}_{plan}_{current_user['id']}_{secrets.token_hex(6)}"
    email = current_user.get("email") or current_user.get("phone") or f"{current_user['id']}@ollie.invalid"

    try:
        response = requests.post(
            f"{API_BASE}/payments",
            headers=_auth_headers(),
            json={
                "tx_ref": tx_ref,
                "amount": price,
                "currency": FLUTTERWAVE_CURRENCY,
                "redirect_url": f"{WEB_APP_URL}/premium/success",
                "payment_plan": plan_id,
                "customer": {"email": email},
                "customizations": {"title": "Ollie Premium"},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        body = response.json()
    except Exception as e:
        logger.error(f"Flutterwave checkout creation failed for user {current_user['id']}: {e}")
        raise HTTPException(status_code=502, detail="Could not start checkout")

    if response.status_code != 200 or body.get("status") != "success":
        logger.error(f"Flutterwave checkout creation rejected for user {current_user['id']}: {body}")
        raise HTTPException(status_code=502, detail="Could not start checkout")

    return {"checkout_url": body["data"]["link"]}


@router.post("/webhook")
async def flutterwave_webhook(request: Request):
    # See the trust-model note at the top of this file -- this header
    # check alone is NOT sufficient proof of authenticity, only a
    # first-pass filter before the real verification below.
    if not FLUTTERWAVE_WEBHOOK_SECRET_HASH or request.headers.get("verif-hash") != FLUTTERWAVE_WEBHOOK_SECRET_HASH:
        raise HTTPException(status_code=400, detail="Invalid signature")

    body = await request.json()
    if body.get("event") == "charge.completed":
        transaction_id = body.get("data", {}).get("id")
        if transaction_id:
            _activate_from_transaction(transaction_id)

    return {"received": True}


def _verify_transaction(transaction_id) -> dict | None:
    try:
        response = requests.get(
            f"{API_BASE}/transactions/{transaction_id}/verify",
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        body = response.json()
    except Exception as e:
        logger.error(f"Flutterwave transaction verification failed for {transaction_id}: {e}")
        return None

    if response.status_code != 200 or body.get("status") != "success":
        return None
    return body.get("data")


def _activate_from_transaction(transaction_id):
    verified = _verify_transaction(transaction_id)
    if not verified or verified.get("status") != "successful":
        return

    parts = (verified.get("tx_ref") or "").split("_")
    if len(parts) < 4 or parts[0] != _TX_REF_PREFIX:
        logger.warning(f"Flutterwave transaction {transaction_id} has an unrecognized tx_ref")
        return
    plan, user_id = parts[1], parts[2]

    expected_price = _price_for(plan)
    charged = verified.get("amount")
    if expected_price is None or charged is None or float(charged) < float(expected_price):
        logger.warning(f"Flutterwave transaction {transaction_id} amount/plan mismatch, refusing to activate")
        return
    if verified.get("currency") != FLUTTERWAVE_CURRENCY:
        logger.warning(f"Flutterwave transaction {transaction_id} currency mismatch, refusing to activate")
        return

    # Payment Plans don't hand back a "current period end" the way a
    # Stripe subscription does -- Flutterwave just auto-charges again
    # on the plan's interval and fires a fresh charge.completed each
    # time. Granting roughly one interval from now (rather than from
    # the plan's own clock) means a late-arriving webhook still gives
    # the user the full period they paid for.
    interval_days = 366 if plan == "yearly" else 31
    expiry_ms = _now_ms() + interval_days * 24 * 60 * 60 * 1000

    sub_data = {
        "user_id": user_id,
        "status": "active",
        "purchase_token": str(transaction_id),
        "product_id": _product_id_for(plan),
        "expiry_time_millis": expiry_ms,
        "source": "flutterwave",
    }

    existing = supabase.table("subscriptions").select("id").eq("user_id", user_id).execute()
    if existing.data:
        supabase.table("subscriptions").update(sub_data).eq("id", existing.data[0]["id"]).execute()
    else:
        supabase.table("subscriptions").insert(sub_data).execute()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
