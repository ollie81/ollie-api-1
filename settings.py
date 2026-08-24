# ============================================================
# SETTINGS — account, usage, notifications, memory management
# ============================================================

import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from database import supabase, OllieDB
from auth import get_current_user

logger = logging.getLogger("ollie.settings")

router = APIRouter()


class NotificationToggleRequest(BaseModel):
    enabled: bool


# ============================================================
# USAGE — how many free messages used today, plan status
# ============================================================

@router.get("/usage")
def get_usage(current_user: dict = Depends(get_current_user)):
    try:
        db = OllieDB()
        user_id = current_user["id"]

        messages_today = db.get_messages_today(user_id)
        has_bonus = db.has_active_ad_bonus(user_id)
        current_streak = db.get_streak(user_id)

        premium_result = supabase.table("subscriptions") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .execute()
        is_premium = len(premium_result.data) > 0

        return {
            "messages_used_today": messages_today,
            "daily_limit": 20,
            "has_active_ad_bonus": has_bonus,
            "is_premium": is_premium,
            "current_streak": current_streak,
            # Same "unset/null defaults to enabled" convention as
            # NotificationService.create_notification's own check --
            # current_user is the full row (get_current_user selects
            # "*"), so this is free, no extra query.
            "notifications_enabled": current_user.get("notifications_enabled") is not False,
        }
    except Exception as e:
        logger.error(f"get_usage failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not load usage")


# ============================================================
# NOTIFICATIONS — enable/disable push notifications
# ============================================================

@router.put("/notifications")
def toggle_notifications(
    req: NotificationToggleRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        supabase.table("users").update({
            "notifications_enabled": req.enabled
        }).eq("id", current_user["id"]).execute()
        return {"success": True, "notifications_enabled": req.enabled}
    except Exception as e:
        logger.error(f"toggle_notifications failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not update notification setting")


# ============================================================
# CLEAR MEMORY — wipe stored memories + interests, keep account
# ============================================================

@router.delete("/memory")
def clear_memory(current_user: dict = Depends(get_current_user)):
    try:
        user_id = current_user["id"]
        supabase.table("memories").delete().eq("user_id", user_id).execute()
        supabase.table("user_interests").delete().eq("user_id", user_id).execute()
        return {"success": True, "message": "Memory cleared"}
    except Exception as e:
        logger.error(f"clear_memory failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not clear memory")


# ============================================================
# DELETE ACCOUNT — permanent, cascades to related data
# ============================================================

@router.delete("/account")
def delete_account(current_user: dict = Depends(get_current_user)):
    """
    Deletes the user row. Relies on ON DELETE CASCADE foreign
    keys (conversations, memories, user_interests, sessions,
    scheduled_events, subscriptions, refresh_tokens, ad_bonus,
    notifications, message_usage, moods, goals) all referencing
    users(id) — verify these cascades exist in Supabase, since a
    failed cascade would leave orphaned rows for a deleted user.
    """
    try:
        user_id = current_user["id"]
        supabase.table("refresh_tokens").delete().eq("user_id", user_id).execute()
        supabase.table("users").delete().eq("id", user_id).execute()
        return {"success": True, "message": "Account deleted"}
    except Exception as e:
        logger.error(f"delete_account failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not delete account")
