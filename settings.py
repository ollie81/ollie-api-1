# ============================================================
# SETTINGS — account, usage, notifications, memory management
# ============================================================

import logging
from typing import Literal
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from database import supabase, OllieDB
from auth import get_current_user
from premium import is_premium_active

logger = logging.getLogger("ollie.settings")

router = APIRouter()


class NotificationToggleRequest(BaseModel):
    enabled: bool


class NotificationFrequencyRequest(BaseModel):
    frequency: Literal["off", "low", "normal", "frequent"]


class LocationUpdateRequest(BaseModel):
    country: str | None = None
    region: str | None = None
    district: str | None = None


class MemoryToggleRequest(BaseModel):
    enabled: bool


class MemoryUpdateRequest(BaseModel):
    memory_text: str | None = None
    category: str | None = None


class DisplayNameRequest(BaseModel):
    name: str


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
        voice_trial_seconds_remaining = db.get_voice_trial_remaining(user_id)

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
            "voice_trial_seconds_remaining": voice_trial_seconds_remaining,
            # Same "unset/null defaults to enabled" convention as
            # NotificationService.create_notification's own check --
            # current_user is the full row (get_current_user selects
            # "*"), so this is free, no extra query.
            "notifications_enabled": current_user.get("notifications_enabled") is not False,
            "notification_frequency": current_user.get("notification_frequency") or "normal",
            "memory_enabled": current_user.get("memory_enabled") is not False,
            "country": current_user.get("country"),
            "region": current_user.get("region"),
            "district": current_user.get("district"),
        }
    except Exception as e:
        logger.error(f"get_usage failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not load usage")


# ============================================================
# DISPLAY NAME — what Ollie calls you. Stored in `username`, which
# is purely a display field already -- auth only ever keys off id/
# phone/email, never username -- so this is safe to let onboarding
# set. Phone and email signups have no real name otherwise (their
# username defaults to their raw phone number or email address);
# Google signups already get this for free from their Google name.
# ============================================================

@router.put("/display-name")
def update_display_name(req: DisplayNameRequest, current_user: dict = Depends(get_current_user)):
    name = req.name.strip()[:50]
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    try:
        supabase.table("users").update({"username": name}).eq("id", current_user["id"]).execute()
        return {"success": True, "username": name}
    except Exception as e:
        logger.error(f"update_display_name failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not update name")


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


@router.put("/notification-frequency")
def update_notification_frequency(
    req: NotificationFrequencyRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Governs how much Ollie reaches out FIRST (morning check-in,
    nightly recap, event check-ins, "you disappeared") -- see
    daily_message.py / event_scheduler.py. Setting this to "off"
    also flips the blunter notifications_enabled master switch off,
    so a single control genuinely means no push at all, matching
    what picking "Off" implies. Reminders (explicit user requests)
    are a separate concern and always send regardless of this.

    "frequent" is Premium-only -- free tier still has off/low/normal,
    nothing is taken away, this just gates the extra-eager tier.
    run_daily_messages also treats a previously-saved "frequent" as
    "normal" for a user who isn't premium (e.g. a lapsed trial), so
    this write-time check isn't the only thing enforcing it.
    """
    if req.frequency == "frequent" and not is_premium_active(current_user["id"]):
        raise HTTPException(status_code=402, detail="Frequent check-ins are an Ollie Premium feature")
    try:
        supabase.table("users").update({
            "notification_frequency": req.frequency,
            "notifications_enabled": req.frequency != "off",
        }).eq("id", current_user["id"]).execute()
        return {"success": True, "notification_frequency": req.frequency}
    except Exception as e:
        logger.error(f"update_notification_frequency failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not update notification frequency")


# ============================================================
# LOCATION — country/region/district, so Ollie can talk like a
# local. Entirely optional; see chat.py's _location_block.
# ============================================================

@router.put("/location")
def update_location(
    req: LocationUpdateRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        supabase.table("users").update({
            "country": req.country,
            "region": req.region,
            "district": req.district,
        }).eq("id", current_user["id"]).execute()
        return {
            "success": True,
            "country": req.country,
            "region": req.region,
            "district": req.district,
        }
    except Exception as e:
        logger.error(f"update_location failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not update location")


# ============================================================
# MEMORIES — view, edit, delete individual memories; toggle
# whether Ollie remembers/uses memory at all. See chat.py's
# memory_enabled gate and memory.py's extract_memory_worthy.
# ============================================================

@router.get("/memories")
def list_memories(current_user: dict = Depends(get_current_user)):
    try:
        db = OllieDB()
        memories = db.get_all_memories(current_user["id"])
        return {"memories": memories}
    except Exception as e:
        logger.error(f"list_memories failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not load memories")


@router.patch("/memories/{memory_id}")
def update_memory(
    memory_id: str,
    req: MemoryUpdateRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        db = OllieDB()
        updated = db.update_memory(
            current_user["id"], memory_id,
            memory_text=req.memory_text, category=req.category,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_memory failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not update memory")


@router.delete("/memories/{memory_id}")
def delete_single_memory(memory_id: str, current_user: dict = Depends(get_current_user)):
    try:
        db = OllieDB()
        deleted = db.delete_memory(current_user["id"], memory_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"delete_single_memory failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not delete memory")


@router.put("/memory/enabled")
def toggle_memory(req: MemoryToggleRequest, current_user: dict = Depends(get_current_user)):
    try:
        supabase.table("users").update({
            "memory_enabled": req.enabled
        }).eq("id", current_user["id"]).execute()
        return {"success": True, "memory_enabled": req.enabled}
    except Exception as e:
        logger.error(f"toggle_memory failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not update memory setting")


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
