from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user
from notification_service import NotificationService

router = APIRouter()

@router.get("/")
def get_notifications(current_user: dict = Depends(get_current_user)):
    """Get user's notifications"""
    try:
        notifications = NotificationService.get_user_notifications(current_user["id"])
        return {"success": True, "notifications": notifications}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Mark notification as read"""
    try:
        NotificationService.mark_as_read(notification_id)
        return {"success": True, "message": "Notification marked as read"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
