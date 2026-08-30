# ============================================================
# DAILY MESSAGE — two proactive daily moments, each once a day
# at a randomized local time within its own window:
#
#   1. MORNING CHECK-IN (~7-11am local) -- personalized using
#      memory/interest context, prioritizing anything specific
#      known about TODAY (a recently-mentioned event, or a rough
#      mood yesterday) over a generic callback.
#   2. NIGHTLY RECAP (~8-11pm local) -- summarizes what actually
#      happened today, grounded in the real conversation
#      transcript so it never fabricates. Silently skipped (no
#      notification sent) on a day with nothing to report --
#      unlike the morning check-in, there is no safe fallback
#      line for "recap of a day that didn't happen."
#
# Both share the same scheduling shape (_due_check), called every
# ~15 minutes by run_daily_messages.
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
from premium import is_premium_active

logger = logging.getLogger("ollie.daily_message")
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Local-time windows each moment can land in.
MORNING_WINDOW_START_HOUR = 7
MORNING_WINDOW_END_HOUR = 11
NIGHTLY_WINDOW_START_HOUR = 20
NIGHTLY_WINDOW_END_HOUR = 23

FALLBACK_LINES = [
    "morning! thinking about you today 💭",
    "hey — just checking in on you 😊",
    "hi! hope today's treating you well",
    "thinking of you — how's it going today?",
    "hey you. just wanted to say hi 👋",
]

# How many days of silence (no user message) before Ollie checks in
# unprompted, per notification_frequency. 'off' never checks in --
# see _process_disappeared_checkin, which reads this.
DISAPPEARED_THRESHOLD_DAYS = {
    "low": 10,
    "normal": 5,
    "frequent": 3,
}

DISAPPEARED_FALLBACK_LINES = [
    "you disappeared 😂 everything good?",
    "hey — haven't heard from you in a bit. everything okay?",
    "hey stranger. just checking in on you",
    "hi! thinking of you — how've you been?",
]


# ============================================================
# MORNING CHECK-IN — generation
# ============================================================

def _generate_morning_checkin(user_id: str, today_local: date) -> str:
    db = OllieDB()
    try:
        memories = db.get_relevant_memories(user_id)
        context = db.get_user_context(user_id)
        memory_block = build_memory_context(memories, context)
        interest_block = build_interest_context(user_id)
        prompt_context = f"{memory_block}\n{interest_block}" if interest_block else memory_block

        extra_lines = []

        # Anything mentioned in the last day and a half that got
        # saved as an "event" memory (see memory.py's categorized
        # extraction) -- this is what lets Ollie say "you said you
        # had that test today" instead of something generic.
        recent_events = db.get_memories_by_category(
            user_id, ["event"], since=datetime.now(timezone.utc) - timedelta(hours=36),
        )
        for event in recent_events[:2]:
            text = (event.get("memory_text") or "").strip()
            if text:
                extra_lines.append(f"RECENTLY MENTIONED, MIGHT BE TODAY: {text}")

        yesterday_mood = db.get_mood_for_date(user_id, today_local - timedelta(days=1))
        if yesterday_mood and yesterday_mood.get("mood"):
            extra_lines.append(f"YESTERDAY'S MOOD: {yesterday_mood['mood']}")

        extra_context = "\n".join(extra_lines)
        full_context = f"{prompt_context}\n{extra_context}" if extra_context else prompt_context

        if not full_context.strip():
            return random.choice(FALLBACK_LINES)

        system_prompt = f"""{OLLIE_PERSONALITY}

{full_context}

It's morning where the user is, and you're reaching out to them FIRST,
unprompted -- this isn't a reply to anything they said. Write ONE short,
warm morning message (max 2 sentences).

If "RECENTLY MENTIONED, MIGHT BE TODAY" is present, prioritize that --
reference the specific thing directly, like a friend who remembered
("you said you had that test today -- want to do a quick review
together?"). Else if "YESTERDAY'S MOOD" reads heavy or rough, gently
check in on that instead ("yesterday sounded rough, feeling any better
today?"). Otherwise, reference something else specific you remember
about them.

No greeting-card language, no "As an AI". Sound like a friend texting
first thing in the morning, not a notification."""

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
            logger.warning(f"morning_checkin: generated message flagged for user {user_id}, using fallback")
            return random.choice(FALLBACK_LINES)

        return content
    except Exception as e:
        logger.warning(f"morning_checkin: generation failed for user {user_id}, using fallback: {e}")
        return random.choice(FALLBACK_LINES)


# ============================================================
# NIGHTLY RECAP — generation
# ============================================================

def _generate_nightly_recap(user_id: str, today_local: date, offset: timedelta) -> str | None:
    """
    Returns a recap grounded in today's actual conversation, or
    None if there's nothing genuine to report (too little happened,
    generation failed, or the model itself found nothing worth
    summarizing) -- silence is the correct behavior here, never a
    generic filler, since a recap that isn't real is worse than no
    recap at all.
    """
    db = OllieDB()
    try:
        today_start_utc = datetime.combine(today_local, time(0, 0), tzinfo=timezone.utc) - offset
        messages_today = db.get_messages_since(user_id, today_start_utc)

        # Fewer than 2 messages isn't a real conversation to recap.
        if len(messages_today) < 2:
            return None

        transcript_lines = [
            f"{'User' if m.get('sender') == 'user' else 'Ollie'}: {m.get('message', '')}"
            for m in messages_today
        ]
        transcript = "\n".join(transcript_lines)[:4000]

        response = openai_client.chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Here is today's actual conversation between Ollie "
                        f"and the user:\n\n{transcript}\n\n"
                        "Write a short, warm 'how today went' recap using "
                        "ONLY what's genuinely in this conversation -- 2 to "
                        "4 short lines, each starting with a dash, no "
                        "preamble, no sign-off. Never invent anything not "
                        "actually present above. If nothing meaningful "
                        "happened (just small talk, nothing worth "
                        "recapping), reply with exactly: NOTHING"
                    )
                },
            ],
            max_completion_tokens=150,
            temperature=0.7,
            timeout=15,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content or content == "NOTHING":
            return None

        if moderate_text(content):
            logger.warning(f"nightly_recap: generated recap flagged for user {user_id}, skipping")
            return None

        return content
    except Exception as e:
        logger.warning(f"nightly_recap: generation failed for user {user_id}, skipping: {e}")
        return None


# ============================================================
# DISAPPEARED CHECK-IN — "you disappeared, everything good?"
# ============================================================
# A re-engagement message for a user who's gone quiet, NOT tied to
# a time window like the other two -- fires whenever enough silent
# days have passed (see DISAPPEARED_THRESHOLD_DAYS), at most once
# per silence (see _process_disappeared_checkin). Explicitly told
# never to guilt-trip, on top of the same rule already in
# OLLIE_PERSONALITY -- this is exactly the message type most prone
# to drifting into "you abandoned me" territory if generated
# carelessly.

def _generate_disappeared_checkin(user_id: str) -> str:
    db = OllieDB()
    try:
        memories = db.get_relevant_memories(user_id)
        context = db.get_user_context(user_id)
        memory_block = build_memory_context(memories, context)

        if not memory_block.strip():
            return random.choice(DISAPPEARED_FALLBACK_LINES)

        system_prompt = f"""{OLLIE_PERSONALITY}

{memory_block}

The user hasn't talked to you in several days. You're reaching out
FIRST, unprompted. Write ONE short, warm "you've been quiet, checking
in" message (max 2 sentences) -- like "you disappeared 😂 everything
good?" Never guilt-trip, never sound needy or clingy, never say
things like "I missed you" or "you abandoned me" or "don't leave me".
If you remember something specific about them, you can reference it
lightly, but the main point is just a casual, warm check-in.

No greeting-card language, no "As an AI"."""

        response = openai_client.chat.completions.create(
            model=FAST_MODEL,
            messages=[{"role": "system", "content": system_prompt}],
            max_completion_tokens=100,
            temperature=1,
            timeout=15,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return random.choice(DISAPPEARED_FALLBACK_LINES)

        if moderate_text(content):
            logger.warning(f"disappeared_checkin: generated message flagged for user {user_id}, using fallback")
            return random.choice(DISAPPEARED_FALLBACK_LINES)

        return content
    except Exception as e:
        logger.warning(f"disappeared_checkin: generation failed for user {user_id}, using fallback: {e}")
        return random.choice(DISAPPEARED_FALLBACK_LINES)


# ============================================================
# SHARED SCHEDULING
# ============================================================

def _window_bounds_utc(local_day: date, offset: timedelta, start_hour: int, end_hour: int) -> tuple[datetime, datetime]:
    start_local = datetime.combine(local_day, time(start_hour, 0), tzinfo=timezone.utc)
    end_local = datetime.combine(local_day, time(end_hour, 0), tzinfo=timezone.utc)
    return start_local - offset, end_local - offset


def _due_check(
    row: dict, now_utc: datetime, start_hour: int, end_hour: int,
    last_sent_key: str, next_at_key: str,
) -> tuple[bool, dict | None, date]:
    """
    Shared "is it time yet" logic for both the morning check-in and
    nightly recap jobs -- picks a random send time within today's
    local window the first time this runs for a user today, then
    reports whether "now" has reached it.

    Returns (due_now, update_dict_or_none, today_local). The caller
    applies update_dict (if not None) regardless of due_now -- it's
    either "here's the target I just picked" or "mark today done",
    never both at once.
    """
    offset = timedelta(minutes=row["last_known_utc_offset_minutes"])
    today_local = (now_utc + offset).date()

    last_sent_str = row.get(last_sent_key)
    if last_sent_str and date.fromisoformat(last_sent_str) == today_local:
        return False, None, today_local  # already sent today

    window_start, window_end = _window_bounds_utc(today_local, offset, start_hour, end_hour)

    target_str = row.get(next_at_key)
    target = OllieDB._parse_utc(target_str) if target_str else None

    # (Re)pick today's target if there isn't one yet, or the stored
    # one isn't for today's window (e.g. left over from a day it
    # never got the chance to fire on).
    if target is None or target < window_start or target >= window_end:
        lower = max(now_utc, window_start)
        if lower >= window_end:
            return False, None, today_local  # today's window already passed
        target = lower + timedelta(seconds=random.uniform(0, (window_end - lower).total_seconds()))
        return False, {next_at_key: target.isoformat()}, today_local

    if now_utc >= target:
        return True, {last_sent_key: today_local.isoformat(), next_at_key: None}, today_local

    return False, None, today_local


def _process_morning_checkin(row: dict, now_utc: datetime) -> None:
    user_id = row["id"]
    due, update, today_local = _due_check(
        row, now_utc, MORNING_WINDOW_START_HOUR, MORNING_WINDOW_END_HOUR,
        "last_daily_message_date", "next_daily_message_at",
    )
    if update:
        supabase.table("users").update(update).eq("id", user_id).execute()
    if due:
        message = _generate_morning_checkin(user_id, today_local)
        NotificationService.create_notification(user_id=user_id, title="Ollie", body=message)


def _process_nightly_recap(row: dict, now_utc: datetime) -> None:
    user_id = row["id"]
    offset = timedelta(minutes=row["last_known_utc_offset_minutes"])
    due, update, today_local = _due_check(
        row, now_utc, NIGHTLY_WINDOW_START_HOUR, NIGHTLY_WINDOW_END_HOUR,
        "last_nightly_recap_date", "next_nightly_recap_at",
    )
    if update:
        supabase.table("users").update(update).eq("id", user_id).execute()
    if due:
        recap = _generate_nightly_recap(user_id, today_local, offset)
        if recap:
            NotificationService.create_notification(user_id=user_id, title="Today with Ollie", body=recap)


def _process_disappeared_checkin(row: dict, now_utc: datetime, frequency: str) -> None:
    user_id = row["id"]
    threshold_days = DISAPPEARED_THRESHOLD_DAYS.get(frequency, DISAPPEARED_THRESHOLD_DAYS["normal"])

    last_message_str = row.get("last_message_at")
    if not last_message_str:
        return  # never talked to Ollie at all -- nothing to "disappear" from
    last_message_at = OllieDB._parse_utc(last_message_str)

    if (now_utc - last_message_at) < timedelta(days=threshold_days):
        return

    last_checkin_str = row.get("last_disappeared_checkin_at")
    if last_checkin_str:
        last_checkin_at = OllieDB._parse_utc(last_checkin_str)
        if last_checkin_at > last_message_at:
            return  # already checked in since they last talked -- never twice in a row

    message = _generate_disappeared_checkin(user_id)
    NotificationService.create_notification(user_id=user_id, title="Ollie", body=message)
    supabase.table("users").update({"last_disappeared_checkin_at": now_utc.isoformat()}).eq("id", user_id).execute()


def run_daily_messages() -> None:
    """
    Call this periodically (e.g. every 15-30 minutes) from a
    scheduler. Every eligible user is independent -- one user's
    failure is logged and never affects another's, or the other
    daily moments for the same user.

    notification_frequency ('off'/'low'/'normal'/'frequent', see
    migration 013) governs how much of this fires, on top of the
    blunter notifications_enabled master switch:
      - off: nothing in this sweep fires.
      - low: morning check-in only -- no nightly recap, no
        disappeared check-in, longest disappeared threshold.
      - normal (default): morning + nightly + disappeared check at
        the default threshold -- today's baseline behavior.
      - frequent: same as normal, with a shorter disappeared
        threshold (checks in sooner after silence). Premium-only --
        see settings.py's write-time gate; a non-premium user with
        "frequent" already saved (e.g. a lapsed trial) is treated as
        "normal" here rather than trusting the stored value forever.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        result = supabase.table("users") \
            .select("id, last_known_utc_offset_minutes, last_daily_message_date, "
                    "next_daily_message_at, last_nightly_recap_date, "
                    "next_nightly_recap_at, notifications_enabled, "
                    "notification_frequency, last_message_at, last_disappeared_checkin_at") \
            .not_.is_("fcm_token", "null") \
            .not_.is_("last_known_utc_offset_minutes", "null") \
            .execute()

        for row in (result.data or []):
            if row.get("notifications_enabled") is False:
                continue

            frequency = row.get("notification_frequency") or "normal"
            if frequency == "off":
                continue
            if frequency == "frequent" and not is_premium_active(row["id"]):
                frequency = "normal"

            try:
                _process_morning_checkin(row, now_utc)
            except Exception as e:
                logger.error(f"run_daily_messages: morning check-in failed for user {row.get('id')}: {e}")

            if frequency == "low":
                continue  # low = morning only, no nightly recap or disappeared check

            try:
                _process_nightly_recap(row, now_utc)
            except Exception as e:
                logger.error(f"run_daily_messages: nightly recap failed for user {row.get('id')}: {e}")

            try:
                _process_disappeared_checkin(row, now_utc, frequency)
            except Exception as e:
                logger.error(f"run_daily_messages: disappeared check-in failed for user {row.get('id')}: {e}")
    except Exception as e:
        logger.error(f"run_daily_messages: query failed: {e}")
