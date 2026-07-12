# ============================================================
# INTEREST MEMORY — learns what a user naturally cares about
# over time (hobbies, topics, passions), separate from the
# existing fact/identity memory system in memory.py.
# ============================================================
# Does NOT touch existing memories, emotional detection,
# language detection, routing, or personality — purely additive.
#
# Requires a Supabase table "user_interests":
#   id (uuid, pk), user_id (uuid, indexed), interest (text),
#   mention_count (integer, default 1), last_mentioned (timestamptz)
#
# Suggested index:
#   create index idx_user_interests_user_id on user_interests(user_id);

import json
import logging
from datetime import datetime

from openai import OpenAI
from config import OPENAI_API_KEY
from database import supabase
from memory import FAST_MODEL

logger = logging.getLogger("ollie.interest_memory")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

MAX_INTERESTS_IN_CONTEXT = 8


# ============================================================
# EXTRACTION
# ============================================================

def extract_interest(text: str) -> str | None:
    """
    Uses the fast model to detect a genuine ongoing interest,
    hobby, or passion mentioned in the message — not hardcoded
    keywords, so it generalizes to anime, coding, football,
    fashion, art, gaming, business, science, or anything else.

    Returns a short lowercase interest label (e.g. "coding",
    "anime", "arsenal football"), or None if nothing meaningful
    is mentioned. Never raises — a failed extraction just means
    no interest gets saved this turn.
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
                        "Decide if this message reveals a genuine interest, "
                        "hobby, or passion the person has — something they "
                        "actively do or follow (anime, coding, a sport/team, "
                        "photography, gaming, books, fashion, art, music, "
                        "business, science, etc). Ignore one-off mentions "
                        "with no real engagement, small talk, and anything "
                        "that isn't really a personal interest.\n\n"
                        "Reply with ONLY a JSON object, no other text:\n"
                        '{"has_interest": true or false, '
                        '"interest": "short 1-3 word lowercase label, '
                        'or empty string"}'
                    )
                },
                {"role": "user", "content": text}
            ],
            max_completion_tokens=60,
            temperature=0,
            timeout=10,
        )

        content = response.choices[0].message.content
        if not content:
            return None

        data = json.loads(content.strip())

        if not data.get("has_interest"):
            return None

        interest = (data.get("interest") or "").strip().lower()

        if not interest or len(interest) > 40:
            return None

        return interest

    except Exception as e:
        logger.warning(f"extract_interest failed, skipping: {e}")
        return None


# ============================================================
# SAVING
# ============================================================

def save_interest(user_id: str, interest: str) -> None:
    """
    Increments mention_count + updates last_mentioned if this
    interest already exists for the user (case-insensitive
    match), otherwise creates a new row. Never raises — a
    failed save shouldn't break the chat flow.
    """
    if not user_id or not interest:
        return

    try:
        existing = (
            supabase.table("user_interests")
            .select("id, mention_count")
            .eq("user_id", user_id)
            .ilike("interest", interest)
            .execute()
        )

        now = datetime.utcnow().isoformat()

        if existing.data:
            row = existing.data[0]
            new_count = row.get("mention_count", 1) + 1
            supabase.table("user_interests").update({
                "mention_count": new_count,
                "last_mentioned": now,
            }).eq("id", row["id"]).execute()
        else:
            supabase.table("user_interests").insert({
                "user_id": user_id,
                "interest": interest,
                "mention_count": 1,
                "last_mentioned": now,
            }).execute()

    except Exception as e:
        logger.warning(f"save_interest failed for user {user_id}: {e}")


def maybe_track_interest(user_id: str, message: str) -> None:
    """
    Entry point called from the chat route: detect + save in
    one step. Isolated in its own try/except so a failure here
    can never break the main chat response.
    """
    try:
        interest = extract_interest(message)
        if interest:
            save_interest(user_id, interest)
    except Exception as e:
        logger.warning(f"maybe_track_interest failed for user {user_id}: {e}")


# ============================================================
# RETRIEVAL
# ============================================================

def get_top_interests(user_id: str, limit: int = MAX_INTERESTS_IN_CONTEXT) -> list[str]:
    """
    Returns the user's top interests, ranked by how often
    they've come up and how recently. Returns [] on any error
    rather than raising.
    """
    if not user_id:
        return []

    try:
        response = (
            supabase.table("user_interests")
            .select("interest")
            .eq("user_id", user_id)
            .order("mention_count", desc=True)
            .order("last_mentioned", desc=True)
            .limit(limit)
            .execute()
        )
        return [row["interest"] for row in (response.data or []) if row.get("interest")]

    except Exception as e:
        logger.warning(f"get_top_interests failed for user {user_id}: {e}")
        return []


def build_interest_context(user_id: str) -> str:
    """
    Formats the user's top interests into a block ready to drop
    into the system prompt. Returns "" if there are none yet, so
    it adds nothing for brand-new users.
    """
    interests = get_top_interests(user_id)
    if not interests:
        return ""

    lines = ["USER INTERESTS:"]
    for interest in interests:
        lines.append(f"  - {interest}")
    return "\n".join(lines)
