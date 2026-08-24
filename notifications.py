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
        user_id = current_user["id"]
        notifications = NotificationService.get_user_notifications(user_id)
        unread_count = NotificationService.unread_count(user_id)
        return {"success": True, "notifications": notifications, "unread_count": unread_count}
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
        updated = NotificationService.mark_as_read(notification_id, current_user["id"])
        if not updated:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"success": True, "message": "Notification marked as read"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"mark_notification_read failed for notification {notification_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not update notification")
