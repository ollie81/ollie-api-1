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
#   4. Create your subscription product in Play Console, and set
#      env var PLAY_SUBSCRIPTION_PRODUCT_ID to its product ID.
#   5. On the Flutter side, use the in_app_purchase package to
#      complete a real purchase, then send its purchaseToken to
#      this endpoint instead of a hardcoded/fake payload.

import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from googleapiclient.discovery import build
from google.oauth2 import service_account

from database import supabase, OllieDB
from auth import get_current_user
from config import (
    GOOGLE_PLAY_SERVICE_ACCOUNT_JSON,
    ANDROID_PACKAGE_NAME,
    PLAY_SUBSCRIPTION_PRODUCT_ID,
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


@router.get("/status")
def premium_status(current_user: dict = Depends(get_current_user)):
    result = supabase.table("subscriptions") \
        .select("*") \
        .eq("user_id", current_user["id"]) \
        .eq("status", "active") \
        .execute()
    return {"is_premium": len(result.data) > 0}


@router.post("/activate")
def activate_premium(data: dict, current_user: dict = Depends(get_current_user)):
    """
    Verifies a real Google Play purchase before granting premium.
    Expects: {"purchase_token": "...", "product_id": "..."} from
    the Flutter in_app_purchase flow.
    """
    purchase_token = data.get("purchase_token")
    product_id = data.get("product_id", PLAY_SUBSCRIPTION_PRODUCT_ID)

    if not purchase_token:
        raise HTTPException(status_code=400, detail="Missing purchase_token")

    try:
        service = _get_play_service()
        result = service.purchases().subscriptions().get(
            packageName=ANDROID_PACKAGE_NAME,
            subscriptionId=product_id,
            token=purchase_token,
        ).execute()
    except Exception as e:
        logger.error(f"Play purchase verification failed for user {current_user['id']}: {e}")
        raise HTTPException(status_code=400, detail="Could not verify purchase")

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
