# ============================================================
# PREMIUM — Premium routes
# ============================================================

from fastapi import APIRouter, Depends
from database import supabase
from auth import get_current_user

router = APIRouter()

@router.get("/status/{phone_number}")
def premium_status(phone_number: str, current_user: dict = Depends(get_current_user)):
    result = supabase.table("subscriptions") \
        .select("*") \
        .eq("user_id", current_user["id"]) \
        .eq("status", "active") \
        .execute()
    return {"is_premium": len(result.data) > 0}

@router.post("/activate")
def activate_premium(data: dict, current_user: dict = Depends(get_current_user)):
    return {"success": True, "message": "Premium activated"}
