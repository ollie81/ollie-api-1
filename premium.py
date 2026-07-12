# ============================================================
# PREMIUM — Premium routes
# ============================================================
from fastapi import APIRouter, Depends
from database import supabase, OllieDB
from auth import get_current_user

router = APIRouter()


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
    return {"success": True, "message": "Premium activated"}


@router.post("/watch-ad")
def watch_ad(current_user: dict = Depends(get_current_user)):
    """
    Call this after the client's ad SDK confirms a rewarded ad
    was fully watched (not just opened/started). Grants a time
    window of unlimited messaging, regardless of daily limit.

    NOTE: this currently trusts the client to only call it after
    a completed ad. For production, verify server-side using your
    ad network's server-side verification (SSV) callback instead —
    otherwise a user could call this endpoint directly without
    ever watching an ad. AdMob SSV is the standard way to close
    this gap; happy to wire that in once you're set up with it.
    """
    db = OllieDB()
    db.grant_ad_bonus(current_user["id"], minutes=10)
    return {"success": True, "message": "bonus messages unlocked", "minutes": 10}
