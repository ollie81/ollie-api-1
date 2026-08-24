# ============================================================
# DAILY MESSAGE — Ollie reaches out first, once a day, at a
# randomized local time within a daytime window. Personalized
# with the same memory/interest context the chat pipeline uses,
# with a safe templated fallback if generation or moderation
# ever fails -- a missed personalized line should never mean a
# missed message entirely.
#
# Two-phase per user per day, both handled by the same periodic
# sweep (send_daily_messages, called every ~15 minutes):
#   1. First tick after their local day starts: pick a random
#      send time within today's window and store it.
#   2. Once "now" reaches that stored time: generate + send,
#      and mark today done.
# ============================================================

import logging
import random
from datetime import date, datetime, time, timedelta, timezone

from openai import OpenAI

from config import OPENAI_API_KEY
from database import OllieDB, supabase
from memory import build_memory_context, moderate_text, FAST_MODEL
from personality import OLLIE_PERSONALITY
from interest_memory import build_interest_context
from notification_service import NotificationService

logger = logging.getLogger("ollie.daily_message")
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Local-time window the message can land in -- never the middle
# of the night.
WINDOW_START_HOUR = 8
WINDOW_END_HOUR = 21

FALLBACK_LINES = [
    "morning! thinking about you today 💭",
    "hey — just checking in on you 😊",
    "hi! hope today's treating you well",
    "thinking of you — how's it going today?",
    "hey you. just wanted to say hi 👋",
]


def _generate_message(user_id: str) -> str:
    db = OllieDB()
    try:
        memories = db.get_relevant_memories(user_id)
        context = db.get_user_context(user_id)
        memory_block = build_memory_context(memories, context)
        interest_block = build_interest_context(user_id)
        prompt_context = f"{memory_block}\n{interest_block}" if interest_block else memory_block

        if not prompt_context.strip():
            return random.choice(FALLBACK_LINES)

        system_prompt = f"""{OLLIE_PERSONALITY}

{prompt_context}

You're reaching out to the user FIRST, unprompted — this isn't a reply
to anything they said. Write ONE short, warm message (max 2 sentences)
that naturally references something specific you remember about them
above (their mood pattern, a goal, something they mentioned). No
greeting-card language, no "As an AI". Sound like a friend texting
first, not a notification."""

        response = openai_client.chat.completions.create(
            model=FAST_MODEL,
            messages=[{"role": "system", "content": system_prompt}],
            max_completion_tokens=120,
            temperature=1,
            timeout=15,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return random.choice(FALLBACK_LINES)

        if moderate_text(content):
            logger.warning(f"daily_message: generated message flagged for user {user_id}, using fallback")
            return random.choice(FALLBACK_LINES)

        return content
    except Exception as e:
        logger.warning(f"daily_message: generation failed for user {user_id}, using fallback: {e}")
        return random.choice(FALLBACK_LINES)


def _window_bounds_utc(local_day: date, offset: timedelta) -> tuple[datetime, datetime]:
    start_local = datetime.combine(local_day, time(WINDOW_START_HOUR, 0), tzinfo=timezone.utc)
    end_local = datetime.combine(local_day, time(WINDOW_END_HOUR, 0), tzinfo=timezone.utc)
    return start_local - offset, end_local - offset


def _process_user(row: dict, now_utc: datetime) -> None:
    user_id = row["id"]
    offset = timedelta(minutes=row["last_known_utc_offset_minutes"])
    today_local = (now_utc + offset).date()

    last_sent_str = row.get("last_daily_message_date")
    if last_sent_str and date.fromisoformat(last_sent_str) == today_local:
        return  # already sent today

    window_start, window_end = _window_bounds_utc(today_local, offset)

    target_str = row.get("next_daily_message_at")
    target = OllieDB._parse_utc(target_str) if target_str else None

    # (Re)pick today's send time if there isn't one yet, or the
    # stored one isn't for today's window (e.g. left over from a
    # day it never got the chance to fire on).
    if target is None or target < window_start or target >= window_end:
        lower = max(now_utc, window_start)
        if lower >= window_end:
            return  # today's window has already passed; try again tomorrow
        target = lower + timedelta(seconds=random.uniform(0, (window_end - lower).total_seconds()))
        supabase.table("users").update({
            "next_daily_message_at": target.isoformat(),
        }).eq("id", user_id).execute()
        return

    if now_utc >= target:
        message = _generate_message(user_id)
        NotificationService.create_notification(user_id=user_id, title="Ollie", body=message)
        supabase.table("users").update({
            "last_daily_message_date": today_local.isoformat(),
            "next_daily_message_at": None,
        }).eq("id", user_id).execute()


def send_daily_messages() -> None:
    """
    Call this periodically (e.g. every 15-30 minutes) from a
    scheduler. Every eligible user is independent -- one user's
    failure is logged and never affects another's.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        result = supabase.table("users") \
            .select("id, last_known_utc_offset_minutes, last_daily_message_date, "
                    "next_daily_message_at, notifications_enabled") \
            .not_.is_("fcm_token", "null") \
            .not_.is_("last_known_utc_offset_minutes", "null") \
            .execute()

        for row in (result.data or []):
            if row.get("notifications_enabled") is False:
                continue
            try:
                _process_user(row, now_utc)
            except Exception as e:
                logger.error(f"send_daily_messages: failed for user {row.get('id')}: {e}")
    except Exception as e:
        logger.error(f"send_daily_messages: query failed: {e}")
