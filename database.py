# ============================================================
# DATABASE — OllieDB class + Supabase client
# ============================================================

from supabase import create_client
from datetime import datetime, date
from config import SUPABASE_URL, SUPABASE_KEY

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

class OllieDB:
    def __init__(self):
        self.supabase = supabase

    def get_or_create_user(self, username: str, email: str = None, phone: str = None):
        response = self.supabase.table("users").select("*").eq("username", username).execute()
        if response.data:
            return response.data[0]
        result = self.supabase.table("users").insert({
            "username": username,
            "email": email,
            "phone": phone,
            "country": "RW"
        }).execute()
        return result.data[0]

    def start_session(self, user_id: str):
        result = self.supabase.table("sessions").insert({
            "user_id": user_id,
            "session_start": datetime.now().isoformat()
        }).execute()
        if not result.data:
            raise Exception("Failed to create session")
        return result.data[0]

    def end_session(self, session_id: str, message_count: int, duration_minutes: int):
        self.supabase.table("sessions").update({
            "session_end": datetime.now().isoformat(),
            "message_count": message_count,
            "duration_minutes": duration_minutes
        }).eq("id", session_id).execute()

    def save_message(self, user_id: str, session_id: str, message: str, sender: str, emotion_score: float = None):
        self.supabase.table("conversations").insert({
            "user_id": user_id,
            "session_id": session_id,
            "message": message,
            "sender": sender,
            "emotion_score": emotion_score,
            "created_at": datetime.now().isoformat()
        }).execute()

    def get_recent_messages(self, user_id: str, limit: int = 10):
        """Rebuild conversation history server-side — fixes amnesia"""
        response = self.supabase.table("conversations") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        messages = list(reversed(response.data))
        history = []
        for msg in messages:
            role = "assistant" if msg["sender"] == "ollie" else "user"
            history.append({"role": role, "content": msg["message"]})
        return history

    def save_memory(self, user_id: str, memory_text: str, importance: int = 1):
        # Check for duplicate before saving
        existing = self.supabase.table("memories") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("memory_text", memory_text) \
            .execute()
        if not existing.data:
            self.supabase.table("memories").insert({
                "user_id": user_id,
                "memory_text": memory_text,
                "importance": importance,
                "is_active": True
            }).execute()

    def get_relevant_memories(self, user_id: str, limit: int = 5):
        response = self.supabase.table("memories") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("is_active", True) \
            .order("importance", desc=True) \
            .limit(limit) \
            .execute()
        return response.data

    def update_mood(self, user_id: str, mood: str, note: str = None):
        today = date.today().isoformat()
        existing = self.supabase.table("moods") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("date", today) \
            .execute()
        mood_data = {"user_id": user_id, "mood": mood, "date": today, "note": note}
        if existing.data:
            self.supabase.table("moods").update(mood_data).eq("id", existing.data[0]["id"]).execute()
        else:
            self.supabase.table("moods").insert(mood_data).execute()

    def get_user_context(self, user_id: str):
        memories = self.get_relevant_memories(user_id)
        today = date.today().isoformat()
        mood = self.supabase.table("moods") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("date", today) \
            .execute()
        goals = self.supabase.table("goals") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .execute()
        return {
            "memories": memories,
            "today_mood": mood.data[0] if mood.data else None,
            "active_goals": goals.data if goals.data else []
        }

    def check_voice_minutes(self, user_id: str):
        response = self.supabase.table("subscriptions") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .execute()
        if not response.data:
            return {"has_minutes": False, "remaining": 0}
        sub = response.data[0]
        remaining = sub.get("voice_minutes_limit", 0) - sub.get("voice_minutes_used", 0)
        return {"has_minutes": remaining > 0, "remaining": remaining}

    def use_voice_minute(self, user_id: str, minutes: int = 1):
        response = self.supabase.table("subscriptions") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .execute()
        if response.data:
            sub = response.data[0]
            new_used = sub.get("voice_minutes_used", 0) + minutes
            self.supabase.table("subscriptions") \
                .update({"voice_minutes_used": new_used}) \
                .eq("id", sub["id"]) \
                .execute()

    def get_messages_today(self, user_id: str) -> int:
        today = date.today().isoformat()
        result = self.supabase.table("message_usage") \
            .select("count") \
            .eq("user_id", user_id) \
            .eq("date", today) \
            .execute()
        return result.data[0].get("count", 0) if result.data else 0

    def can_send_message(self, user_id: str, limit: int = 50) -> bool:
        return self.get_messages_today(user_id) < limit

    def increment_message_count(self, user_id: str):
        today = date.today().isoformat()
        existing = self.supabase.table("message_usage") \
            .select("count") \
            .eq("user_id", user_id) \
            .eq("date", today) \
            .execute()
        if existing.data:
            self.supabase.table("message_usage") \
                .update({"count": existing.data[0]["count"] + 1}) \
                .eq("user_id", user_id) \
                .eq("date", today) \
                .execute()
        else:
            self.supabase.table("message_usage").insert({
                "user_id": user_id,
                "date": today,
                "count": 1
            }).execute()

    def get_voice_minutes_today(self, user_id: str) -> float:
        today = date.today().isoformat()
        result = self.supabase.table("voice_usage") \
            .select("minutes_used") \
            .eq("user_id", user_id) \
            .eq("date", today) \
            .execute()
        return sum(item["minutes_used"] for item in result.data) if result.data else 0.0

    def can_use_voice(self, user_id: str) -> bool:
        return self.get_voice_minutes_today(user_id) < 1.0
