import logging

from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user
from notification_service import NotificationService

logger = logging.getLogger("ollie.notifications_route")

router = APIRouter()

@router.get("/")
def get_notifications(current_user: dict = Depends(get_current_user)):
    """Get user's notifications"""
    try:
        notifications = NotificationService.get_user_notifications(current_user["id"])
        return {"success": True, "notifications": notifications}
    except Exception as e:
        logger.error(f"get_notifications failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not load notifications")

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
        logger.error(f"mark_notification_read failed for notification {notification_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not update notification")
