# ============================================================
# EVENT SCHEDULER — detects meaningful future events in chat
# and schedules a genuine check-in notification later.
# ============================================================
# Replaces regex keyword matching with a model call, so it
# catches ANY meaningful future event (interview, exam, date,
# deadline, family event...) not just a fixed hospital/doctor
# keyword list — and works in any language the user writes in.
#
# Anti-spam design:
#   - only runs when the message already scored importance >= 2
#     (reuses your existing memory importance scoring)
#   - never runs on crisis-flagged messages
#   - dedups by topic so the same event doesn't get scheduled twice
#   - caps total Ollie-initiated check-in notifications per user per day
#     (explicit reminders below are NOT subject to this cap)
#
# Requires a Supabase table "scheduled_events" with columns:
#   id (uuid, pk), user_id (text), event_summary (text),
#   topic_key (text), scheduled_for (timestamptz),
#   status (text: 'pending' | 'sent' | 'skipped'),
#   kind (text: 'checkin' | 'reminder', default 'checkin'),
#   created_at (timestamptz)
#
# Requires: pip install apscheduler  (add to requirements.txt)

import json
import logging
import hashlib
from datetime import datetime, timedelta

from openai import OpenAI
from config import OPENAI_API_KEY
from database import supabase
from memory import FAST_MODEL, CRISIS_KEYWORDS
from notification_service import NotificationService

logger = logging.getLogger("ollie.event_scheduler")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Max Ollie-initiated CHECK-IN notifications per user per day.
# Does not apply to explicit reminders (see below) — those are
# things the user directly asked for, not proactive check-ins.
MAX_CHECKINS_PER_USER_PER_DAY = 1

# Sanity bounds on how far out a check-in can be scheduled.
MIN_HOURS = 1
MAX_HOURS = 24 * 30  # 30 days


# ============================================================
# DETECTION — passive check-ins
# ============================================================

def detect_future_event(text: str) -> dict | None:
    """
    Ask the fast model whether this message describes a
    meaningful future event worth checking in on later — an
    interview, exam, first date, big decision, deadline,
    family event, anything with emotional weight — not just
    medical appointments.

    Returns None on any failure or if no event is found, so a
    detection miss never breaks the chat flow.
    """
    if not text or not text.strip():
        return None

    try:
        response = openai_client.chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Decide if this message mentions a meaningful "
                        "future event the person might want a caring "
                        "check-in about afterward — e.g. a job interview, "
                        "exam, first date, big decision, deadline, family "
                        "event, court date, travel. Ignore routine/trivial "
                        "mentions and anything already in the past.\n\n"
                        "Reply with ONLY a JSON object, no other text:\n"
                        '{"has_future_event": true or false, '
                        '"event_summary": "short neutral description, or empty string", '
                        '"hours_until_checkin": integer estimate of how many '
                        "hours from now until it's appropriate to check in "
                        "(e.g. ~24 for \"tomorrow\", ~72 for \"in 3 days\", "
                        "~168 for \"next week\"; use 24 if unsure)}"
                    )
                },
                {"role": "user", "content": text}
            ],
            max_completion_tokens=120,
            temperature=0,
            timeout=10,
        )

        content = response.choices[0].message.content
        if not content:
            return None

        data = json.loads(content.strip())

        if not data.get("has_future_event"):
            return None

        summary = (data.get("event_summary") or "").strip()
        hours = data.get("hours_until_checkin")

        if not summary or not isinstance(hours, (int, float)):
            return None

        hours = max(MIN_HOURS, min(int(hours), MAX_HOURS))

        return {"event_summary": summary, "hours_until_checkin": hours}

    except Exception as e:
        logger.warning(f"detect_future_event failed, skipping: {e}")
        return None


def _is_crisis_message(text: str) -> bool:
    text_lower = (text or "").lower()
    return any(kw in text_lower for kw in CRISIS_KEYWORDS)


def maybe_schedule_event(user_id: str, message: str, importance: int) -> None:
    """
    Entry point called from the chat route. Only calls the model
    (costs money/latency) when the message already scored as
    memory-worthy at importance >= 2, and never on crisis-flagged
    messages — crisis handling belongs in-conversation, not in a
    delayed push notification.
    """
    try:
        if importance < 2:
            return
        if _is_crisis_message(message):
            return

        event = detect_future_event(message)
        if not event:
            return

        schedule_event_notification(
            user_id=user_id,
            event_summary=event["event_summary"],
            hours_until_checkin=event["hours_until_checkin"],
        )

    except Exception as e:
        logger.warning(f"maybe_schedule_event failed for user {user_id}: {e}")


# ============================================================
# SCHEDULING (storage)
# ============================================================

def _topic_key(user_id: str, event_summary: str) -> str:
    raw = f"{user_id}:{event_summary.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def schedule_event_notification(user_id: str, event_summary: str, hours_until_checkin: int) -> bool:
    """
    Stores a pending check-in for later. Dedups on (user, topic)
    so the same event mentioned twice doesn't create two reminders.
    Returns True if scheduled, False if skipped (duplicate or error).
    """
    try:
        topic_key = _topic_key(user_id, event_summary)

        existing = (
            supabase.table("scheduled_events")
            .select("id")
            .eq("user_id", user_id)
            .eq("topic_key", topic_key)
            .in_("status", ["pending", "sent"])
            .execute()
        )
        if existing.data:
            logger.info(f"schedule_event_notification: duplicate topic for user {user_id}, skipping")
            return False

        scheduled_for = (datetime.utcnow() + timedelta(hours=hours_until_checkin)).isoformat()

        supabase.table("scheduled_events").insert({
            "user_id": user_id,
            "event_summary": event_summary,
            "topic_key": topic_key,
            "scheduled_for": scheduled_for,
            "status": "pending",
            "kind": "checkin",
            "created_at": datetime.utcnow().isoformat(),
        }).execute()

        logger.info(f"scheduled check-in for user {user_id} at {scheduled_for}: {event_summary}")
        return True

    except Exception as e:
        logger.error(f"schedule_event_notification failed for user {user_id}: {e}")
        return False


# ============================================================
# EXPLICIT REMINDERS — "remind me to X" / "don't let me forget"
# Separate from the passive check-in detector above: this does
# NOT require importance >= 2, and has no 1-hour minimum delay.
# ============================================================

def detect_explicit_reminder(text: str) -> dict | None:
    if not text or not text.strip():
        return None

    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    try:
        response = openai_client.chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Current UTC time: {now_utc}. Decide if the user "
                        "is explicitly asking to be reminded of something "
                        "(\"remind me to...\", \"don't let me forget...\", "
                        "\"set a reminder for...\"). If so, work out how "
                        "many minutes from now the reminder should fire, "
                        "based on whatever time they gave (relative like "
                        "\"in 20 minutes\", or a clock time like \"at 5pm\" "
                        "— compute minutes until that next occurs).\n\n"
                        "Reply with ONLY JSON, no other text:\n"
                        '{"is_reminder": true or false, '
                        '"reminder_text": "short description, or empty string", '
                        '"minutes_until": integer}'
                    )
                },
                {"role": "user", "content": text}
            ],
            max_completion_tokens=100,
            temperature=0,
            timeout=10,
        )
        content = response.choices[0].message.content
        if not content:
            return None
        data = json.loads(content.strip())
        if not data.get("is_reminder"):
            return None
        reminder_text = (data.get("reminder_text") or "").strip()
        minutes = data.get("minutes_until")
        if not reminder_text or not isinstance(minutes, (int, float)):
            return None
        minutes = max(1, min(int(minutes), MAX_HOURS * 60))
        return {"reminder_text": reminder_text, "minutes_until": minutes}
    except Exception as e:
        logger.warning(f"detect_explicit_reminder failed, skipping: {e}")
        return None


def maybe_schedule_reminder(user_id: str, message: str) -> None:
    try:
        reminder = detect_explicit_reminder(message)
        if not reminder:
            return

        scheduled_for = (
            datetime.utcnow() + timedelta(minutes=reminder["minutes_until"])
        ).isoformat()

        supabase.table("scheduled_events").insert({
            "user_id": user_id,
            "event_summary": reminder["reminder_text"],
            "topic_key": _topic_key(user_id, f"reminder:{reminder['reminder_text']}:{scheduled_for}"),
            "scheduled_for": scheduled_for,
            "status": "pending",
            "kind": "reminder",
            "created_at": datetime.utcnow().isoformat(),
        }).execute()

        logger.info(f"scheduled explicit reminder for user {user_id} at {scheduled_for}")

    except Exception as e:
        logger.warning(f"maybe_schedule_reminder failed for user {user_id}: {e}")


# ============================================================
# SENDING (runs on a periodic scheduler tick)
# ============================================================

def _checkins_sent_today(user_id: str) -> int:
    since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    response = (
        supabase.table("scheduled_events")
        .select("id")
        .eq("user_id", user_id)
        .eq("status", "sent")
        .gte("scheduled_for", since)
        .execute()
    )
    return len(response.data) if response.data else 0


def run_due_notifications() -> None:
    """
    Call this periodically (e.g. every 10 minutes) from a
    scheduler. Finds due check-ins AND reminders. Reminders
    always send (they're explicit user requests); check-ins
    still respect the daily cap.
    """
    try:
        now = datetime.utcnow().isoformat()
        due = (
            supabase.table("scheduled_events")
            .select("*")
            .eq("status", "pending")
            .lte("scheduled_for", now)
            .execute()
        )

        for row in (due.data or []):
            user_id = row.get("user_id")
            event_summary = row.get("event_summary", "")
            row_id = row.get("id")
            kind = row.get("kind", "checkin")

            try:
                if kind == "checkin" and _checkins_sent_today(user_id) >= MAX_CHECKINS_PER_USER_PER_DAY:
                    logger.info(f"run_due_notifications: daily cap hit for user {user_id}, deferring")
                    continue  # leave pending, retry next tick / next day

                if kind == "reminder":
                    title = "Ollie reminding you"
                    body = event_summary
                else:
                    title = "Ollie checking in"
                    body = f"hey — how did it go with {event_summary}?"

                NotificationService.create_notification(
                    user_id=user_id,
                    title=title,
                    body=body,
                )

                supabase.table("scheduled_events").update(
                    {"status": "sent"}
                ).eq("id", row_id).execute()

            except Exception as e:
                logger.error(f"run_due_notifications: failed to send for user {user_id}: {e}")

    except Exception as e:
        logger.error(f"run_due_notifications: query failed: {e}")
