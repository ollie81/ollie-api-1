from datetime import datetime
import uuid
from database import notifications_collection, users_collection

class NotificationService:
    
    @staticmethod
    def create_notification(user_id: str, title: str, body: str):
        """Create and immediately send notification"""
        notification_id = str(uuid.uuid4())
        
        notification = {
            "id": notification_id,
            "user_id": user_id,
            "title": title,
            "body": body,
            "is_read": False,
            "created_at": datetime.utcnow(),
            "sent": True,
        }
        
        notifications_collection.insert_one(notification)
        
        # Send push if user has FCM token
        user = users_collection.find_one({"user_id": user_id})
        if user and user.get("fcm_token"):
            from notification_service import send_push  # avoid circular import
            send_push(user["fcm_token"], title, body)
        
        return notification_id
    
    @staticmethod
    def get_user_notifications(user_id: str, limit: int = 20):
        return list(notifications_collection.find(
            {"user_id": user_id}
        ).sort("created_at", -1).limit(limit))
    
    @staticmethod
    def mark_as_read(notification_id: str):
        notifications_collection.update_one(
            {"id": notification_id},
            {"$set": {"is_read": True}}
        )
