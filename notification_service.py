from datetime import datetime
import uuid
import requests

from database import supabase

# ============================================================
# FIREBASE CLOUD MESSAGING
# ============================================================

FCM_SERVER_KEY = "YOUR_FIREBASE_SERVER_KEY"


def send_push(fcm_token: str, title: str, body: str):
    """
    Send push notification through Firebase Cloud Messaging.
    """

    if not fcm_token:
        return False

    headers = {
        "Authorization": f"key={FCM_SERVER_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "to": fcm_token,
        "notification": {
            "title": title,
            "body": body,
            "sound": "default"
        },
        "priority": "high"
    }

    try:
        response = requests.post(
            "https://fcm.googleapis.com/fcm/send",
            json=payload,
            headers=headers,
            timeout=10,
        )

        return response.status_code == 200

    except Exception as e:
        print("Push error:", e)
        return False


# ============================================================
# NOTIFICATION SERVICE
# ============================================================

class NotificationService:

    @staticmethod
    def create_notification(user_id: str, title: str, body: str):

        notification_id = str(uuid.uuid4())

        notification = {
            "id": notification_id,
            "user_id": user_id,
            "title": title,
            "body": body,
            "is_read": False,
            "sent": True,
            "created_at": datetime.utcnow().isoformat()
        }

        supabase.table("notifications").insert(notification).execute()

        user = (
            supabase.table("users")
            .select("fcm_token")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if user.data:
            token = user.data.get("fcm_token")
            if token:
                send_push(token, title, body)

        return notification_id

    @staticmethod
    def get_user_notifications(user_id: str, limit: int = 20):

        response = (
            supabase.table("notifications")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        return response.data or []

    @staticmethod
    def mark_as_read(notification_id: str):

        (
            supabase.table("notifications")
            .update({"is_read": True})
            .eq("id", notification_id)
            .execute()
        )

    @staticmethod
    def mark_all_as_read(user_id: str):

        (
            supabase.table("notifications")
            .update({"is_read": True})
            .eq("user_id", user_id)
            .eq("is_read", False)
            .execute()
        )

    @staticmethod
    def unread_count(user_id: str):

        response = (
            supabase.table("notifications")
            .select("id")
            .eq("user_id", user_id)
            .eq("is_read", False)
            .execute()
        )

        return len(response.data) if response.data else 0

    @staticmethod
    def delete_notification(notification_id: str):

        (
            supabase.table("notifications")
            .delete()
            .eq("id", notification_id)
            .execute()
        )
