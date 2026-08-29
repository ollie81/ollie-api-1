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
from datetime import datetime, timedelta, timezone

from openai import OpenAI
from config import OPENAI_API_KEY
from database import supabase
from memory import FAST_MODEL, FLAGSHIP_MODEL, CRISIS_KEYWORDS, moderate_text
from personality import OLLIE_PERSONALITY
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

# How soon after scheduling a reminder a follow-up message can
# still be treated as correcting it (see _try_correct_recent_reminder)
# rather than an unrelated later message.
REMINDER_CORRECTION_WINDOW_MINUTES = 3


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

        scheduled_for = (datetime.now(timezone.utc) + timedelta(hours=hours_until_checkin)).isoformat()

        supabase.table("scheduled_events").insert({
            "user_id": user_id,
            "event_summary": event_summary,
            "topic_key": topic_key,
            "scheduled_for": scheduled_for,
            "status": "pending",
            "kind": "checkin",
            "created_at": datetime.now(timezone.utc).isoformat(),
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

def _compute_reminder_datetime(parsed: dict, utc_offset_minutes: int | None, now_utc: datetime) -> datetime | None:
    """
    Turns the model's structured, low-arithmetic extraction into
    an actual UTC datetime. All the real date math happens here,
    in code, not in the model's head — asking a fast model to
    directly compute "minutes from now" itself requires it to do
    timezone conversion + relative-time math in one shot, which is
    exactly the kind of multi-step arithmetic small/fast models get
    wrong, silently landing reminders at the wrong time (including
    in the past, which then gets clamped to "fire in 1 minute").
    """
    time_type = parsed.get("time_type")

    if time_type == "relative":
        minutes = parsed.get("relative_minutes")
        if not isinstance(minutes, (int, float)):
            return None
        return now_utc + timedelta(minutes=max(1, int(minutes)))

    if time_type != "absolute":
        return None

    hour = parsed.get("absolute_hour")
    minute = parsed.get("absolute_minute")
    if not isinstance(hour, (int, float)) or not isinstance(minute, (int, float)):
        return None
    hour, minute = int(hour), int(minute)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    days_from_today = parsed.get("days_from_today")
    if not isinstance(days_from_today, (int, float)) or days_from_today < 0:
        days_from_today = 0
    days_from_today = int(days_from_today)

    offset = utc_offset_minutes or 0
    # Wall-clock local time, kept as a UTC-tagged datetime purely
    # so arithmetic works — converted back to true UTC at the end.
    local_now = now_utc + timedelta(minutes=offset)
    target_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0) \
        + timedelta(days=days_from_today)

    # No specific future day was given and that clock time already
    # passed today — they obviously meant the next occurrence, not
    # right now or the past.
    if days_from_today == 0 and target_local <= local_now:
        target_local += timedelta(days=1)

    return target_local - timedelta(minutes=offset)


def _looks_like_reminder_request(text: str) -> bool:
    """
    Cheap keyword pre-check, used only to decide whether a missed
    detection is worth a second, more expensive attempt -- not a
    replacement for the real (language-aware) LLM detection below,
    just a trigger for retrying it.
    """
    text_lower = (text or "").lower()
    return any(kw in text_lower for kw in ("remind", "reminder", "don't forget", "dont forget"))


def _extract_reminder(text: str, utc_offset_minutes: int | None, now_utc_dt: datetime, model: str) -> dict | None:
    now_utc = now_utc_dt.strftime("%Y-%m-%d %H:%M UTC")

    if utc_offset_minutes is not None:
        local_dt = now_utc_dt + timedelta(minutes=utc_offset_minutes)
        sign = "+" if utc_offset_minutes >= 0 else "-"
        offset_str = f"UTC{sign}{abs(utc_offset_minutes) // 60:02d}:{abs(utc_offset_minutes) % 60:02d}"
        local_time_line = (
            f"The user's local time right now is {local_dt.strftime('%Y-%m-%d %H:%M')} "
            f"({offset_str}), a {local_dt.strftime('%A')}. A clock time like "
            f"\"5pm\" means 5pm in THEIR local time."
        )
    else:
        local_time_line = (
            "The user's local timezone is unknown — treat any clock time they "
            "give as their local time; it'll be assumed UTC as a fallback."
        )

    response = openai_client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    f"Current UTC time: {now_utc}. {local_time_line}\n\n"
                    "Decide if the user is explicitly asking to be "
                    "reminded of something (\"remind me to...\", "
                    "\"don't let me forget...\", \"don't forget to "
                    "remind me...\", \"set a reminder for...\").\n\n"
                    "If a RELATIVE duration is given (\"in 20 minutes\", "
                    "\"after 10 minutes\", \"in 2 hours\", \"after an "
                    "hour\"), set time_type to \"relative\" and fill "
                    "relative_minutes with just the raw number of "
                    "minutes — no other math. \"in\" and \"after\" both "
                    "mean the same thing here: minutes from now.\n\n"
                    "If an ABSOLUTE clock time is given (\"at 5pm\", "
                    "\"at 9:30\"), set time_type to \"absolute\" and "
                    "fill absolute_hour (0-23) and absolute_minute "
                    "(0-59) in THEIR local time, plus days_from_today "
                    "(0 = today/unspecified, 1 = tomorrow, 2 = day "
                    "after, etc). Do not compute how far away that is "
                    "— just report the clock time and day, as given.\n\n"
                    "Reply with ONLY JSON, no other text:\n"
                    '{"is_reminder": true or false, '
                    '"reminder_text": "short description, or empty string", '
                    '"time_type": "relative" or "absolute", '
                    '"relative_minutes": integer or null, '
                    '"absolute_hour": integer or null, '
                    '"absolute_minute": integer or null, '
                    '"days_from_today": integer or null}'
                )
            },
            {"role": "user", "content": text}
        ],
        max_completion_tokens=150,
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
    if not reminder_text:
        return None

    target_dt = _compute_reminder_datetime(data, utc_offset_minutes, now_utc_dt)
    if target_dt is None:
        return None

    minutes_until = (target_dt - now_utc_dt).total_seconds() / 60
    minutes_until = max(1, min(int(minutes_until), MAX_HOURS * 60))
    return {"reminder_text": reminder_text, "minutes_until": minutes_until}


def detect_explicit_reminder(text: str, utc_offset_minutes: int | None = None) -> dict | None:
    """
    Ollie's actual chat reply is a SEPARATE model call from this
    one (see get_ollie_response in chat.py) -- it can confidently
    promise "I'll remind you" from conversational context alone,
    with no idea whether this function actually parsed the same
    message as a reminder. That means a miss here silently breaks
    a promise the user just heard, with no feedback that anything
    went wrong.

    To cut down on that: a first pass on the fast model that comes
    back empty gets retried once on the flagship model, but ONLY
    when the message contains an obvious reminder cue (see
    _looks_like_reminder_request) -- otherwise every ordinary
    message would pay for a second, pricier call just to confirm
    it isn't a reminder. Doesn't guarantee a catch, but a slower,
    stronger second look at an obvious miss is worth the cost.
    """
    if not text or not text.strip():
        return None

    now_utc_dt = datetime.now(timezone.utc)

    try:
        result = _extract_reminder(text, utc_offset_minutes, now_utc_dt, FAST_MODEL)
        if result is not None:
            return result
    except Exception as e:
        logger.warning(f"detect_explicit_reminder: fast-model pass failed: {e}")

    if not _looks_like_reminder_request(text):
        return None

    try:
        logger.info("detect_explicit_reminder: fast-model miss on a likely reminder, retrying with flagship model")
        return _extract_reminder(text, utc_offset_minutes, now_utc_dt, FLAGSHIP_MODEL)
    except Exception as e:
        logger.warning(f"detect_explicit_reminder: flagship retry failed, skipping: {e}")
        return None


def _personalize_reminder(reminder_text: str) -> str:
    """
    Turns the bare extracted reminder ("drink water") into a line
    that actually sounds like Ollie said it, not a flat label --
    the notification is the only thing the user sees at the moment
    the reminder fires, so it shouldn't read like a system alert.

    Generated once, at schedule time rather than send time: keeps
    run_due_notifications (which processes every due row for every
    user in one sweep) fast and independent of OpenAI being up,
    and this call is no different from the other LLM side-effects
    (mood, goal, future-event detection) that already run
    synchronously in the same chat turn before the reply returns.

    Falls back to a simple templated line if generation or
    moderation ever has an issue -- a slightly-less-charming
    reminder beats a missing one.
    """
    fallback = f"hey — don't forget: {reminder_text}"
    try:
        response = openai_client.chat.completions.create(
            model=FAST_MODEL,
            messages=[{
                "role": "system",
                "content": f"""{OLLIE_PERSONALITY}

You're sending a push notification delivering a reminder the user
asked for earlier. Write ONE short, warm line (max 1 sentence) that
delivers this reminder: "{reminder_text}"

This is a notification, not a reply in a conversation -- no greeting,
just the reminder itself, in your voice. No "As an AI", no corporate
tone, no quotation marks around it."""
            }],
            max_completion_tokens=60,
            temperature=1,
            timeout=10,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return fallback

        if moderate_text(content):
            logger.warning(f"_personalize_reminder: generated line flagged, using fallback for: {reminder_text}")
            return fallback

        return content
    except Exception as e:
        logger.warning(f"_personalize_reminder: generation failed, using fallback: {e}")
        return fallback


def _looks_like_reminder_correction(text: str) -> bool:
    """
    Cheap keyword pre-check, same role as _looks_like_reminder_request
    -- only decides whether it's worth even looking for a recently
    scheduled reminder to correct, not a real detector by itself.
    """
    text_lower = (text or "").lower()
    return any(kw in text_lower for kw in ("i mean", "i meant", "meant to say", "not what i", "correction"))


def _extract_reminder_correction(previous_text: str, message: str) -> str | None:
    """
    Given the text a reminder was just scheduled with, and a
    follow-up message that might be correcting it, returns the
    corrected reminder text -- or None if this message isn't
    actually correcting that reminder after all.
    """
    response = openai_client.chat.completions.create(
        model=FAST_MODEL,
        messages=[{
            "role": "system",
            "content": (
                f'A reminder was just scheduled with this text: "{previous_text}". '
                "The next message the same user sent might be correcting what "
                "that reminder should actually say (e.g. a typo fix or "
                '"I mean X not Y"). If it IS such a correction, reply with '
                "ONLY the corrected reminder text, short, same style as the "
                "original. If it's NOT correcting that reminder, reply with "
                "ONLY an empty string."
            )
        }, {"role": "user", "content": message}],
        max_completion_tokens=40,
        temperature=0,
        timeout=10,
    )
    content = response.choices[0].message.content
    if content is None:
        return None
    return content.strip() or None


def _try_correct_recent_reminder(user_id: str, message: str, now_utc_dt: datetime) -> bool:
    """
    Handles a real gap: each message is checked independently for
    whether IT is a new reminder request, so a typo in one message
    ("remind me to go to it in 10 minutes") followed by a
    correction in the very next one ("I mean to eat not to it")
    left the reminder scheduled with the typo'd text forever --
    the correction was never itself an explicit reminder request,
    so detect_explicit_reminder just ignored it.

    Only even looks (a DB query, then an LLM call) when the cheap
    keyword gate matches AND this user has a reminder scheduled in
    the last REMINDER_CORRECTION_WINDOW_MINUTES -- so it costs
    nothing on ordinary messages, and can't reach back and "correct"
    something scheduled long ago. Returns True if a reminder was
    updated.
    """
    if not _looks_like_reminder_correction(message):
        return False

    cutoff = (now_utc_dt - timedelta(minutes=REMINDER_CORRECTION_WINDOW_MINUTES)).isoformat()
    recent = (
        supabase.table("scheduled_events")
        .select("id, event_summary")
        .eq("user_id", user_id)
        .eq("kind", "reminder")
        .eq("status", "pending")
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not recent.data:
        return False

    row = recent.data[0]

    try:
        corrected_text = _extract_reminder_correction(row["event_summary"], message)
    except Exception as e:
        logger.warning(f"_try_correct_recent_reminder: extraction failed for user {user_id}: {e}")
        return False

    if not corrected_text:
        return False

    supabase.table("scheduled_events").update({
        "event_summary": corrected_text,
        "notification_body": _personalize_reminder(corrected_text),
    }).eq("id", row["id"]).execute()

    logger.info(f"corrected reminder for user {user_id}: {row['event_summary']!r} -> {corrected_text!r}")
    return True


def maybe_schedule_reminder(user_id: str, message: str, utc_offset_minutes: int | None = None) -> None:
    try:
        reminder = detect_explicit_reminder(message, utc_offset_minutes)
        if not reminder:
            _try_correct_recent_reminder(user_id, message, datetime.now(timezone.utc))
            return

        scheduled_for = (
            datetime.now(timezone.utc) + timedelta(minutes=reminder["minutes_until"])
        ).isoformat()

        supabase.table("scheduled_events").insert({
            "user_id": user_id,
            "event_summary": reminder["reminder_text"],
            "notification_body": _personalize_reminder(reminder["reminder_text"]),
            "topic_key": _topic_key(user_id, f"reminder:{reminder['reminder_text']}:{scheduled_for}"),
            "scheduled_for": scheduled_for,
            "status": "pending",
            "kind": "reminder",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

        logger.info(f"scheduled explicit reminder for user {user_id} at {scheduled_for}")

    except Exception as e:
        logger.warning(f"maybe_schedule_reminder failed for user {user_id}: {e}")


# ============================================================
# SENDING (runs on a periodic scheduler tick)
# ============================================================

def _checkins_sent_today(user_id: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
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
        now = datetime.now(timezone.utc).isoformat()
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
                    # notification_body is the personality-voiced
                    # line generated at schedule time (see
                    # _personalize_reminder) -- event_summary stays
                    # the raw text, only as a fallback for rows
                    # scheduled before that column existed.
                    body = row.get("notification_body") or event_summary
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
