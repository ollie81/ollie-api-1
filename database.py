# ============================================================
# DATABASE — OllieDB class + Supabase client
# ============================================================

from supabase import create_client
from datetime import datetime, date, timedelta, timezone
from config import SUPABASE_URL, SUPABASE_KEY

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Max rewarded-ad watches per user per day. Prevents someone
# from bypassing the daily message limit indefinitely by
# watching ads back-to-back.
MAX_AD_WATCHES_PER_DAY = 3

# Gap of inactivity after which the next message starts a new
# session instead of continuing the last one. 30 minutes matches
# the common analytics-industry default (e.g. Google Analytics).
SESSION_GAP_MINUTES = 30

# Free lifetime voice trial -- "hear Ollie speak real replies"
# rather than the fixed canned /speak/preview line, capped so it
# stays a taste rather than an ongoing free tier.
TRIAL_VOICE_SECONDS_LIMIT = 60

# Rough estimate of spoken characters per second (~150 wpm, ~6
# chars/word including the space) -- good enough for budgeting a
# one-time trial, not meant to be exact.
ESTIMATED_CHARS_PER_SECOND = 15

# Flat per-turn cost charged against the same trial budget for
# /chat/voice (mic input). Charged before the Whisper call rather
# than derived from the transcribed text afterward -- by the time
# transcription finishes, the expensive part (Whisper) already
# happened, so there'd be nothing left to gate.
VOICE_INPUT_TRIAL_COST_SECONDS = 10


def estimate_speech_seconds(text: str) -> int:
    """
    Rough spoken-duration estimate from character count, used to
    budget the /speak free voice trial before synthesis happens.
    Not meant to be exact -- see ESTIMATED_CHARS_PER_SECOND.
    """
    return max(1, round(len(text) / ESTIMATED_CHARS_PER_SECOND))


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
            "phone": phone
        }).execute()
        return result.data[0]

    def start_session(self, user_id: str):
        result = self.supabase.table("sessions").insert({
            "user_id": user_id,
            "session_start": datetime.now(timezone.utc).isoformat()
        }).execute()
        if not result.data:
            raise Exception("Failed to create session")
        return result.data[0]

    def end_session(self, session_id: str, message_count: int, duration_minutes: int, ended_at: datetime = None):
        self.supabase.table("sessions").update({
            "session_end": (ended_at or datetime.now(timezone.utc)).isoformat(),
            "message_count": message_count,
            "duration_minutes": duration_minutes
        }).eq("id", session_id).execute()

    def _last_message_time(self, session_id: str):
        result = self.supabase.table("conversations") \
            .select("created_at") \
            .eq("session_id", session_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        return self._parse_utc(result.data[0]["created_at"]) if result.data else None

    def get_or_create_session(self, user_id: str) -> str:
        """
        Reuses the user's still-open session if their last message
        was within SESSION_GAP_MINUTES; otherwise closes it out —
        dated to that last message, not now, so a session closed
        out late (the user's next message might be days later)
        still gets an accurate end time and duration — and starts
        a fresh one.
        """
        open_result = self.supabase.table("sessions") \
            .select("*") \
            .eq("user_id", user_id) \
            .is_("session_end", "null") \
            .order("session_start", desc=True) \
            .limit(1) \
            .execute()

        if open_result.data:
            session = open_result.data[0]
            reference_time = self._last_message_time(session["id"]) or self._parse_utc(session["session_start"])

            if datetime.now(timezone.utc) - reference_time < timedelta(minutes=SESSION_GAP_MINUTES):
                return session["id"]

            messages = self.supabase.table("conversations") \
                .select("id") \
                .eq("session_id", session["id"]) \
                .execute()
            message_count = len(messages.data) if messages.data else 0
            started_at = self._parse_utc(session["session_start"])
            duration_minutes = max(0, int((reference_time - started_at).total_seconds() // 60))
            self.end_session(session["id"], message_count, duration_minutes, ended_at=reference_time)

        return self.start_session(user_id)["id"]

    def update_streak(self, user_id: str, utc_offset_minutes: int | None) -> int:
        """
        Call once per incoming message. Increments the user's daily
        streak the first time they talk to Ollie on a new local
        calendar day, resets it to 1 if a day was missed, and
        leaves it alone if they've already been credited today.

        Also bumps total_active_days in the same step -- a lifetime
        count of distinct days talked to Ollie that, unlike the
        streak, never resets on a missed day. This is the "duration"
        half of the relationship-stage signal (see relationship.py) —
        deliberately not the streak itself, since a missed day
        should never set the relationship back.

        Uses the user's LOCAL date (from utc_offset_minutes), not
        the server's — a streak that rolls over at UTC midnight
        would feel wrong for anyone outside UTC. Falls back to UTC
        if the client didn't send an offset.
        """
        offset = timedelta(minutes=utc_offset_minutes or 0)
        today_local = (datetime.now(timezone.utc) + offset).date()

        result = self.supabase.table("users") \
            .select("current_streak, last_streak_date, total_active_days") \
            .eq("id", user_id) \
            .single() \
            .execute()
        row = result.data or {}
        current_streak = row.get("current_streak") or 0
        last_date_str = row.get("last_streak_date")
        last_date = date.fromisoformat(last_date_str) if last_date_str else None

        if last_date == today_local:
            return current_streak

        new_streak = current_streak + 1 if last_date == today_local - timedelta(days=1) else 1
        total_active_days = (row.get("total_active_days") or 0) + 1

        self.supabase.table("users").update({
            "current_streak": new_streak,
            "last_streak_date": today_local.isoformat(),
            "total_active_days": total_active_days,
        }).eq("id", user_id).execute()

        return new_streak

    def remember_utc_offset(self, user_id: str, utc_offset_minutes: int | None) -> None:
        """
        Best-effort — called on every /chat and /chat/voice request
        so the daily-message background job (which has no live
        request to read an offset from) has a reasonably fresh one
        to compute each user's local time from. A failure here
        should never break the chat reply.
        """
        if utc_offset_minutes is None:
            return
        try:
            self.supabase.table("users").update({
                "last_known_utc_offset_minutes": utc_offset_minutes,
            }).eq("id", user_id).execute()
        except Exception:
            pass

    def get_streak(self, user_id: str) -> int:
        result = self.supabase.table("users") \
            .select("current_streak") \
            .eq("id", user_id) \
            .single() \
            .execute()
        return (result.data or {}).get("current_streak") or 0

    def save_message(self, user_id: str, session_id: str, message: str, sender: str, emotion_score: float = None):
        self.supabase.table("conversations").insert({
            "user_id": user_id,
            "session_id": session_id,
            "message": message,
            "sender": sender,
            "emotion_score": emotion_score,
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()

    def get_conversation_history(self, user_id: str, limit: int = 50):
        """
        Returns past messages in chronological order (oldest first)
        for the chat UI to render on load — separate from
        get_recent_messages, which formats for the LLM prompt.
        """
        response = self.supabase.table("conversations") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        return list(reversed(response.data))

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

    def save_memory(self, user_id: str, memory_text: str, importance: int = 1, category: str | None = None):
        # Check for duplicate before saving
        existing = self.supabase.table("memories") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("memory_text", memory_text) \
            .execute()
        if not existing.data:
            row = {
                "user_id": user_id,
                "memory_text": memory_text,
                "importance": importance,
                "is_active": True,
            }
            if category:
                row["category"] = category
            self.supabase.table("memories").insert(row).execute()

    def get_relevant_memories(self, user_id: str, limit: int = 5):
        response = self.supabase.table("memories") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("is_active", True) \
            .order("importance", desc=True) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        return response.data

    def get_all_memories(self, user_id: str, limit: int = 200):
        """
        Unfiltered (not capped to the top few by importance) list for
        the Settings memory management screen -- most recent first.
        """
        response = self.supabase.table("memories") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("is_active", True) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        return response.data or []

    def update_memory(self, user_id: str, memory_id: str, memory_text: str | None = None, category: str | None = None) -> bool:
        """Scoped to user_id so one user can't edit another's memory by guessing an id."""
        updates = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if memory_text is not None:
            updates["memory_text"] = memory_text
        if category is not None:
            updates["category"] = category
        result = self.supabase.table("memories") \
            .update(updates) \
            .eq("id", memory_id) \
            .eq("user_id", user_id) \
            .execute()
        return bool(result.data)

    def delete_memory(self, user_id: str, memory_id: str) -> bool:
        """Scoped to user_id -- see update_memory."""
        result = self.supabase.table("memories") \
            .delete() \
            .eq("id", memory_id) \
            .eq("user_id", user_id) \
            .execute()
        return bool(result.data)

    def get_memories_by_category(self, user_id: str, categories: list[str], since: datetime | None = None, limit: int = 10) -> list[dict]:
        """
        Used by the morning check-in to find anything recently
        mentioned that got saved as an "event" memory (see
        memory.py's categorized extraction) -- the connective tissue
        behind "you said you had that test today". since, when
        given, bounds the lookup to avoid resurfacing something
        mentioned weeks ago as if it were happening now.
        """
        query = self.supabase.table("memories") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("is_active", True) \
            .in_("category", categories) \
            .order("created_at", desc=True) \
            .limit(limit)
        if since:
            query = query.gte("created_at", since.isoformat())
        return query.execute().data or []

    def get_mood_for_date(self, user_id: str, date_obj: date) -> dict | None:
        """Read-only lookup for a specific past date -- see update_mood, which is today-only."""
        result = self.supabase.table("moods") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("date", date_obj.isoformat()) \
            .execute()
        return result.data[0] if result.data else None

    def get_messages_since(self, user_id: str, since_utc: datetime, limit: int = 200) -> list[dict]:
        """
        Used by the nightly recap to ground itself in what was
        ACTUALLY said today, rather than reconstructing the day from
        scattered memory/mood/goal signals -- summarizing a real
        transcript is the most reliable way to never fabricate.
        """
        response = self.supabase.table("conversations") \
            .select("sender, message, created_at") \
            .eq("user_id", user_id) \
            .gte("created_at", since_utc.isoformat()) \
            .order("created_at") \
            .limit(limit) \
            .execute()
        return response.data or []

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

    def save_goal(self, user_id: str, title: str) -> None:
        # Dedup against existing active goals (case-insensitive),
        # same shape as save_memory/save_interest, so mentioning
        # the same goal again doesn't create duplicates.
        existing = self.supabase.table("goals") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .ilike("title", title) \
            .execute()
        if not existing.data:
            self.supabase.table("goals").insert({
                "user_id": user_id,
                "title": title,
                "status": "active",
            }).execute()

    def complete_goal(self, user_id: str, title: str) -> bool:
        """
        Marks an active goal completed -- exact-title match, scoped
        to this user. Returns whether a row actually matched.
        """
        result = self.supabase.table("goals") \
            .update({
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }) \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .eq("title", title) \
            .execute()
        return bool(result.data)

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

    def get_journey_summary(self, user_id: str) -> dict:
        """
        Everything the "Our Space" screen needs in one call: how
        many memories Ollie has (a count query, not a full fetch --
        the screen doesn't need the rows, just the number), goals
        (active + completed, for "goals they've worked on"), and a
        curated slice of memories worth looking back on -- the
        more moment-like categories, not plain identity/preference
        facts that are more reference data than "a moment in our
        story" (those stay visible via the full Manage Memories
        list instead).
        """
        memory_count_result = self.supabase.table("memories") \
            .select("id", count="exact") \
            .eq("user_id", user_id) \
            .eq("is_active", True) \
            .execute()
        memory_count = memory_count_result.count or 0

        goals_result = self.supabase.table("goals") \
            .select("*") \
            .eq("user_id", user_id) \
            .in_("status", ["active", "completed"]) \
            .order("completed_at", desc=True) \
            .execute()
        goals = goals_result.data or []
        active_goals = [g for g in goals if g.get("status") == "active"]
        completed_goals = [g for g in goals if g.get("status") == "completed"]

        highlights_result = self.supabase.table("memories") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("is_active", True) \
            .in_("category", ["accomplishment", "struggle", "person", "event", "promise"]) \
            .order("importance", desc=True) \
            .order("created_at", desc=True) \
            .limit(30) \
            .execute()
        highlights = highlights_result.data or []

        return {
            "memory_count": memory_count,
            "active_goals": active_goals,
            "completed_goals": completed_goals,
            "highlights": highlights,
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

    # ============================================================
    # DAILY MESSAGE LIMIT + AD BONUS
    # ============================================================

    def _get_usage_row(self, user_id: str):
        today = date.today().isoformat()
        result = self.supabase.table("message_usage") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("date", today) \
            .execute()
        return result.data[0] if result.data else None

    def get_messages_today(self, user_id: str) -> int:
        row = self._get_usage_row(user_id)
        return row.get("count", 0) if row else 0

    @staticmethod
    def _parse_utc(iso_str: str) -> datetime:
        """
        Parses a stored ISO timestamp as UTC even if it's missing a
        timezone marker (e.g. an old row saved before timezone-aware
        writes were added). Prevents 'naive vs aware' comparison
        crashes on data written by an older version of this code.
        """
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def try_consume_message(self, user_id: str, limit: int = 20) -> bool:
        """
        Atomically checks-and-increments the free-tier daily message
        count in one step. A plain read-then-write (check the count,
        then separately increment it) lets two concurrent requests
        both read the same count and both pass, since neither sees
        the other's increment before making its own decision -- this
        instead only applies the increment if the count still
        matches what was just read (optimistic concurrency), so a
        losing request notices and retries against the fresh value
        instead of silently over-granting.

        Returns False (count left untouched) once the limit is hit.
        Callers that already know the user is premium should call
        increment_message_count directly instead -- this always
        enforces limit, it doesn't know about premium itself (same
        split as before).
        """
        today = date.today().isoformat()

        for _ in range(5):
            row = self._get_usage_row(user_id)

            if row and row.get("ad_bonus_until"):
                if self._parse_utc(row["ad_bonus_until"]) > datetime.now(timezone.utc):
                    return True  # active ad-bonus window, unlimited -- nothing to track

            if not row:
                try:
                    self.supabase.table("message_usage").insert({
                        "user_id": user_id, "date": today, "count": 1,
                    }).execute()
                    return True
                except Exception:
                    continue  # someone else's insert for today already landed -- re-read and retry

            count = row.get("count", 0)
            if count >= limit:
                return False

            result = self.supabase.table("message_usage") \
                .update({"count": count + 1}) \
                .eq("user_id", user_id) \
                .eq("date", today) \
                .eq("count", count) \
                .execute()
            if result.data:
                return True
            # else: count changed under us since the read above -- someone else incremented first, retry

        # Heavy contention exhausted the retry budget -- fail closed
        # rather than risk over-granting.
        return False

    def try_consume_voice_trial(self, user_id: str, estimated_seconds: int) -> bool:
        """
        Atomic check-and-consume against the free lifetime voice
        trial (TRIAL_VOICE_SECONDS_LIMIT), same optimistic-
        concurrency shape as try_consume_message -- estimated_seconds
        is deducted, but only if the running total still matches
        what was just read, so two taps in quick succession can't
        both read the same remaining budget and both pass.

        Callers decide their own cost: /speak estimates from the
        reply text (see estimate_speech_seconds), /chat/voice
        charges a flat VOICE_INPUT_TRIAL_COST_SECONDS up front so
        it isn't left trusting a client-reported duration or
        waiting on the (already-expensive) transcription just to
        learn the text length.

        Returns False (nothing consumed) if estimated_seconds would
        exceed the remaining budget -- including when it's already
        exhausted. Callers should only call this for non-premium
        users; it always enforces the trial cap, it doesn't know
        about premium itself.
        """
        for _ in range(5):
            result = self.supabase.table("users") \
                .select("voice_trial_seconds_used") \
                .eq("id", user_id) \
                .single() \
                .execute()
            used = (result.data or {}).get("voice_trial_seconds_used") or 0

            if used + estimated_seconds > TRIAL_VOICE_SECONDS_LIMIT:
                return False

            update_result = self.supabase.table("users") \
                .update({"voice_trial_seconds_used": used + estimated_seconds}) \
                .eq("id", user_id) \
                .eq("voice_trial_seconds_used", used) \
                .execute()
            if update_result.data:
                return True
            # else: used changed under us since the read above -- someone else consumed first, retry

        # Heavy contention exhausted the retry budget -- fail closed
        # rather than risk over-granting.
        return False

    def get_voice_trial_remaining(self, user_id: str) -> int:
        """
        Read-only -- no race-condition concern like try_consume_voice_trial
        above, this is purely for display (e.g. a "you have 47s left"
        indicator client-side).
        """
        result = self.supabase.table("users") \
            .select("voice_trial_seconds_used") \
            .eq("id", user_id) \
            .single() \
            .execute()
        used = (result.data or {}).get("voice_trial_seconds_used") or 0
        return max(0, TRIAL_VOICE_SECONDS_LIMIT - used)

    def has_active_ad_bonus(self, user_id: str) -> bool:
        row = self._get_usage_row(user_id)
        if row and row.get("ad_bonus_until"):
            return self._parse_utc(row["ad_bonus_until"]) > datetime.now(timezone.utc)
        return False

    def grant_ad_bonus(self, user_id: str, minutes: int = 10) -> bool:
        """
        Grants a temporary window of unlimited messaging after a
        completed rewarded ad. Capped at MAX_AD_WATCHES_PER_DAY so
        someone can't bypass the daily limit by watching ads
        back-to-back all day. Returns False if the cap is hit.

        Same optimistic-concurrency shape as try_consume_message /
        try_consume_voice_trial -- a plain read-then-write here
        would let two concurrent grants (a retried request after a
        dropped response, or a client calling the endpoint directly
        without watching another ad) both read the same ads_watched
        and both get through, since neither sees the other's write
        before deciding.
        """
        today = date.today().isoformat()
        bonus_until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()

        for _ in range(5):
            row = self._get_usage_row(user_id)
            ads_watched = row.get("ads_watched", 0) if row else 0

            if ads_watched >= MAX_AD_WATCHES_PER_DAY:
                return False

            if not row:
                try:
                    self.supabase.table("message_usage").insert({
                        "user_id": user_id, "date": today, "count": 0,
                        "ad_bonus_until": bonus_until, "ads_watched": 1,
                    }).execute()
                    return True
                except Exception:
                    continue  # someone else's insert for today already landed -- re-read and retry

            result = self.supabase.table("message_usage") \
                .update({"ad_bonus_until": bonus_until, "ads_watched": ads_watched + 1}) \
                .eq("user_id", user_id) \
                .eq("date", today) \
                .eq("ads_watched", ads_watched) \
                .execute()
            if result.data:
                return True
            # else: ads_watched changed under us since the read above -- someone else granted first, retry

        # Heavy contention exhausted the retry budget -- fail closed
        # rather than risk over-granting.
        return False

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
