# ============================================================
# PREMIUM — Premium routes
# ============================================================
#
# SETUP REQUIRED before /activate works:
#   1. In Google Play Console: Setup > API access > link a
#      Google Cloud project, create a service account with
#      "View financial data" + "Manage orders and subscriptions"
#      permission, and download its JSON key.
#   2. Set env var GOOGLE_PLAY_SERVICE_ACCOUNT_JSON to the full
#      contents of that JSON key (as a string).
#   3. Set env var ANDROID_PACKAGE_NAME to your real package
#      name (e.g. com.yourcompany.ollie) once you've renamed it.
#   4. Create three products in Play Console -- monthly and yearly
#      auto-renewing subscriptions, plus a managed (one-time)
#      product for lifetime -- matching the IDs in the Flutter
#      client's purchase_service.dart. The backend only needs its
#      own copy of the lifetime one (PLAY_LIFETIME_PRODUCT_ID) since
#      that's the only one verified differently -- see /activate.
#   5. On the Flutter side, use the in_app_purchase package to
#      complete a real purchase, then send its purchaseToken to
#      this endpoint instead of a hardcoded/fake payload.

import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from googleapiclient.discovery import build
from google.oauth2 import service_account

from database import supabase, OllieDB
from auth import get_current_user
from config import (
    GOOGLE_PLAY_SERVICE_ACCOUNT_JSON,
    ANDROID_PACKAGE_NAME,
    PLAY_MONTHLY_PRODUCT_ID,
    PLAY_LIFETIME_PRODUCT_ID,
)

logger = logging.getLogger("ollie.premium")
router = APIRouter()


def _get_play_service():
    if not GOOGLE_PLAY_SERVICE_ACCOUNT_JSON:
        raise HTTPException(
            status_code=500,
            detail="Play billing not configured — GOOGLE_PLAY_SERVICE_ACCOUNT_JSON is not set",
        )
    info = json.loads(GOOGLE_PLAY_SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/androidpublisher"]
    )
    return build("androidpublisher", "v3", credentials=creds)


def is_premium_active(user_id: str) -> bool:
    """
    The real premium check — used by the /status route below, and
    by anything else that needs to gate a feature on subscription
    status (e.g. voice, which is premium-only). Re-verifies with
    Play directly when the locally stored expiry has passed, rather
    than trusting a possibly-stale local timestamp; fails open on
    any verification error so an infra hiccup never strands a
    paying user.
    """
    result = supabase.table("subscriptions") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("status", "active") \
        .execute()

    if not result.data:
        return False

    sub = result.data[0]
    expiry_ms = sub.get("expiry_time_millis") or 0
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # No expiry on record (legacy row), or not yet expired — active.
    if expiry_ms <= 0 or now_ms < expiry_ms:
        return True

    # Locally stored expiry has passed. That could mean the sub
    # genuinely ended, or it auto-renewed on Google's side and we
    # never heard about it (no Real-time Developer Notifications
    # webhook wired up yet) — re-check with Play directly rather
    # than trust our possibly-stale local timestamp.
    try:
        service = _get_play_service()
        play_result = service.purchases().subscriptions().get(
            packageName=ANDROID_PACKAGE_NAME,
            subscriptionId=sub.get("product_id") or PLAY_MONTHLY_PRODUCT_ID,
            token=sub["purchase_token"],
        ).execute()

        payment_state = play_result.get("paymentState", 0)
        new_expiry_ms = int(play_result.get("expiryTimeMillis", 0))
        still_active = payment_state in (1, 2) and now_ms < new_expiry_ms

        supabase.table("subscriptions").update({
            "status": "active" if still_active else "expired",
            "expiry_time_millis": new_expiry_ms,
        }).eq("id", sub["id"]).execute()

        return still_active

    except Exception as e:
        # Can't confirm either way (misconfigured creds, network,
        # quota) — fail open rather than cutting off a paying user
        # over an infra hiccup.
        logger.warning(
            f"is_premium_active: Play re-verification failed for user "
            f"{user_id}, treating as still active: {e}"
        )
        return True


@router.get("/status")
def premium_status(current_user: dict = Depends(get_current_user)):
    return {"is_premium": is_premium_active(current_user["id"])}


@router.post("/activate")
def activate_premium(data: dict, current_user: dict = Depends(get_current_user)):
    """
    Verifies a real Google Play purchase before granting premium.
    Expects: {"purchase_token": "...", "product_id": "..."} from
    the Flutter in_app_purchase flow. product_id must be one of the
    three configured product IDs (monthly/yearly subscription, or
    the one-time lifetime product) -- each is verified differently.
    """
    purchase_token = data.get("purchase_token")
    product_id = data.get("product_id")

    if not purchase_token:
        raise HTTPException(status_code=400, detail="Missing purchase_token")
    if not product_id:
        raise HTTPException(status_code=400, detail="Missing product_id")

    is_lifetime = product_id == PLAY_LIFETIME_PRODUCT_ID

    try:
        service = _get_play_service()
        if is_lifetime:
            # Managed (one-time, non-consumable) product -- no expiry,
            # verified via the products API rather than subscriptions.
            result = service.purchases().products().get(
                packageName=ANDROID_PACKAGE_NAME,
                productId=product_id,
                token=purchase_token,
            ).execute()
        else:
            result = service.purchases().subscriptions().get(
                packageName=ANDROID_PACKAGE_NAME,
                subscriptionId=product_id,
                token=purchase_token,
            ).execute()
    except Exception as e:
        logger.error(f"Play purchase verification failed for user {current_user['id']}: {e}")
        raise HTTPException(status_code=400, detail="Could not verify purchase")

    if is_lifetime:
        # purchaseState: 0 = purchased, 1 = canceled, 2 = pending.
        if result.get("purchaseState", 1) != 0:
            raise HTTPException(status_code=400, detail="Purchase not yet completed")
        expiry_ms = 0  # no expiry -- is_premium_active treats this as active forever
    else:
        # paymentState: 1 = received, 2 = free trial. 0 = pending, don't grant yet.
        payment_state = result.get("paymentState", 0)
        if payment_state not in (1, 2):
            raise HTTPException(status_code=400, detail="Purchase not yet completed")
        expiry_ms = int(result.get("expiryTimeMillis", 0))

    existing = supabase.table("subscriptions") \
        .select("id") \
        .eq("user_id", current_user["id"]) \
        .execute()

    sub_data = {
        "user_id": current_user["id"],
        "status": "active",
        "purchase_token": purchase_token,
        "product_id": product_id,
        "expiry_time_millis": expiry_ms,
    }

    if existing.data:
        supabase.table("subscriptions").update(sub_data).eq("id", existing.data[0]["id"]).execute()
    else:
        supabase.table("subscriptions").insert(sub_data).execute()

    return {"success": True, "message": "Premium activated"}


@router.post("/watch-ad")
def watch_ad(current_user: dict = Depends(get_current_user)):
    """
    Call this after the client's ad SDK confirms a rewarded ad
    was fully watched (not just opened/started). Grants a time
    window of unlimited messaging, up to MAX_AD_WATCHES_PER_DAY
    times per day.

    NOTE: this currently trusts the client to only call it after
    a completed ad. For production, verify server-side using
    AdMob's Server-Side Verification (SSV) callback instead —
    otherwise a user could call this endpoint directly without
    ever watching an ad.
    """
    db = OllieDB()
    granted = db.grant_ad_bonus(current_user["id"], minutes=10)
    if not granted:
        raise HTTPException(status_code=429, detail="Daily ad-watch limit reached")
    return {"success": True, "message": "bonus messages unlocked", "minutes": 10}
