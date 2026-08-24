# ============================================================
# NOTIFICATION SERVICE — FCM HTTP v1 (current API)
# ============================================================
# The old https://fcm.googleapis.com/fcm/send + server-key
# approach was fully shut down by Google in mid-2024. This uses
# the current v1 endpoint, authenticated with a Firebase service
# account JSON (the one you already have as a Railway variable).
#
# Expects an env var containing the FULL service account JSON
# as a string — set FIREBASE_CREDENTIALS_JSON in Railway to the
# contents of that JSON file. If your variable is named
# differently, change FIREBASE_CREDENTIALS_JSON below to match.
#
# Requires: pip install google-auth  (add to requirements.txt)

import os
import json
import logging
import uuid
from datetime import datetime

import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest

from database import supabase

logger = logging.getLogger("ollie.notifications")

FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON")
FCM_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]

_credentials = None
_project_id = None


def _load_credentials():
    """
    Parse the service account JSON once and cache it. Returns
    None (and logs) if the env var is missing or malformed,
    rather than crashing app startup.
    """
    global _credentials, _project_id

    if _credentials is not None:
        return _credentials

    if not FIREBASE_CREDENTIALS_JSON:
        logger.error("FIREBASE_CREDENTIALS_JSON env var not set — push notifications disabled")
        return None

    try:
        info = json.loads(FIREBASE_CREDENTIALS_JSON)
        _credentials = service_account.Credentials.from_service_account_info(
            info, scopes=FCM_SCOPES
        )
        _project_id = info.get("project_id")
        if not _project_id:
            logger.error("Firebase credentials JSON has no project_id field")
            return None
        return _credentials
    except Exception as e:
        logger.error(f"Failed to parse FIREBASE_CREDENTIALS_JSON: {e}")
        return None


def _get_access_token():
    """
    Returns a valid OAuth2 access token, refreshing if expired.
    google-auth caches and reuses the token internally until it
    nears expiry, so this is cheap to call on every push.
    """
    creds = _load_credentials()
    if not creds:
        return None

    try:
        if not creds.valid:
            creds.refresh(GoogleAuthRequest())
        return creds.token
    except Exception as e:
        logger.error(f"Failed to refresh Firebase access token: {e}")
        return None


def send_push(fcm_token: str, title: str, body: str) -> bool:
    """
    Send a push notification via FCM HTTP v1. Returns True/False
    rather than raising, so a failed push never breaks the
    calling request.
    """
    if not fcm_token:
        return False

    access_token = _get_access_token()
    if not access_token or not _project_id:
        logger.error("send_push: no valid Firebase credentials, skipping push")
        return False

    url = f"https://fcm.googleapis.com/v1/projects/{_project_id}/messages:send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; UTF-8",
    }
    payload = {
        "message": {
            "token": fcm_token,
            "notification": {
                "title": title,
                "body": body,
            },
            "android": {"priority": "high"},
            "apns": {"headers": {"apns-priority": "10"}},
        }
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            return True
        logger.warning(f"send_push: FCM returned {response.status_code}: {response.text}")
        return False
    except requests.RequestException as e:
        logger.error(f"send_push: request failed: {e}")
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

        try:
            supabase.table("notifications").insert(notification).execute()
        except Exception as e:
            logger.error(f"create_notification: failed to insert row for user {user_id}: {e}")
            raise

        try:
            user = (
                supabase.table("users")
                .select("fcm_token, notifications_enabled")
                .eq("id", user_id)
                .single()
                .execute()
            )
            # notifications_enabled defaults to unset/true for
            # existing users who've never touched the toggle in
            # Settings -- only an explicit False should suppress
            # the push. The in-app notification row above is saved
            # either way, so it's still visible in-app.
            if user.data and user.data.get("notifications_enabled") is not False:
                token = user.data.get("fcm_token")
                if token:
                    send_push(token, title, body)
        except Exception as e:
            # A failed push shouldn't fail the whole notification —
            # the row is already saved, the user can see it in-app.
            logger.warning(f"create_notification: push failed for user {user_id}: {e}")

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
