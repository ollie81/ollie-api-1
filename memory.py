
# ============================================================
# MEMORY — Language detection + memory helpers (production)
# MEMORY — Language detection + memory helpers (production)
# ============================================================

import json
import logging
import time

from openai import OpenAI
from config import OPENAI_API_KEY

logger = logging.getLogger("ollie.memory")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# MODEL TIERS
# ============================================================
# FLAGSHIP: used whenever quality actually matters — uncommon
# languages, emotional weight, or memory/pattern reasoning.
# FAST: used only for routine small talk in a common language
# with no memory context in play.

FLAGSHIP_MODEL = "gpt-5.5"
FAST_MODEL = "gpt-4.1-nano"

# ============================================================
# LANGUAGE DETECTION
# ============================================================

def detect_language(text: str, max_retries: int = 2) -> str:
    """
    Detect the language of `text` using the flagship model.
    Kept on flagship deliberately: this call is tiny (5 output
    tokens) so cost is negligible, but a wrong detection here
    silently breaks routing and the reply language downstream —
    accuracy matters more than saving a fraction of a cent.

    Retries on transient API errors before falling back to
    english. Every failure is logged, not swallowed silently.
    """
    if not text or not text.strip():
        return "english"

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = openai_client.chat.completions.create(
                model=FLAGSHIP_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Identify the language the person INTENDS to "
                            "write in, even if there are typos, missing "
                            "accents, or phonetic/misspelled words — go "
                            "by the closest real language, not literal "
                            "spelling. Return ONLY the language name in "
                            "lowercase, one word. Examples: english, "
                            "french, kinyarwanda, swahili, arabic, "
                            "spanish, korean, chinese, russian. "
                            "Nothing else."
                        )
                    },
                    {"role": "user", "content": text}
                ],
                max_completion_tokens=80,
                reasoning_effort="none",
                timeout=10,
            )
            content = response.choices[0].message.content
            if not content:
                logger.warning("detect_language: empty content from model, defaulting to english")
                return "english"

            detected = content.strip().lower()
            # basic sanity check — guard against the model returning
            # a sentence instead of a single word
            if len(detected) > 30 or " " in detected:
                logger.warning(f"detect_language: suspicious output '{detected}', defaulting to english")
                return "english"

            return detected

        except Exception as e:
            last_error = e
            logger.warning(f"detect_language attempt {attempt + 1} failed: {e}")
            if attempt < max_retries:
                time.sleep(0.5 * (attempt + 1))  # small backoff

    logger.error(f"detect_language: all retries exhausted, defaulting to english. Last error: {last_error}")
    return "english"

# ============================================================
# TOP-10 MOST SPOKEN LANGUAGES (by total speakers)
# ============================================================
# Anything NOT in this set routes to the flagship model, since
# smaller models are noticeably weaker outside these languages.
# Tune this list if your user base skews differently.

TOP_LANGUAGES = {
    "english", "mandarin", "chinese", "hindi", "spanish",
    "french", "arabic", "bengali", "portuguese", "russian", "urdu"
}

# ============================================================
# EMOTIONAL INTENSITY DETECTION
# ============================================================
# Heuristic, not a model call — keeps routing fast and free.
# Mirrors the emotion-reading rules in the personality prompt.
# NOTE: this is a first-pass heuristic, not a validated
# classifier. Log routing decisions in production and review
# a sample regularly — keyword lists are always leaky until
# you've seen them fail on real conversations.

CRISIS_KEYWORDS = [
    "kill myself", "suicide", "end it all", "want to die",
    "hurt myself", "self harm", "no reason to live", "can't go on",
    "cant go on", "better off dead", "end my life"
]

HEAVY_EMOTION_KEYWORDS = [
    "i'm so sad", "im so sad", "everything is ruined", "i feel alone",
    "i hate my life", "i'm depressed", "im depressed", "i give up",
    "heartbroken", "devastated", "i can't do this anymore",
    "i cant do this anymore", "i messed up so bad", "i'm scared",
    "im scared", "abuse", "hit me", "he hurt me", "she hurt me"
]

HIGH_JOY_KEYWORDS = [
    "i got the job", "i passed", "we won", "best day ever",
    "i'm engaged", "im engaged", "i got in", "i did it",
    "so proud of myself"
]


def is_high_emotional_intensity(text: str) -> bool:
    """
    Cheap heuristic flag for 'this message deserves the flagship
    model's nuance' — crisis language, heavy emotion, big wins,
    or strong non-verbal signals (short flat replies, all caps,
    repeated punctuation).

    Returns False on empty/invalid input rather than raising —
    this function must never crash the request pipeline.
    """
    try:
        if not text:
            return False

        text_lower = text.lower().strip()
        if not text_lower:
            return False

        for kw in CRISIS_KEYWORDS:
            if kw in text_lower:
                return True

        for kw in HEAVY_EMOTION_KEYWORDS:
            if kw in text_lower:
                return True

        for kw in HIGH_JOY_KEYWORDS:
            if kw in text_lower:
                return True

        # short flat message like "." or "k" — often a drained signal
        if len(text_lower) <= 2:
            return True

        # shouting / high energy
        letters = [c for c in text if c.isalpha()]
        if letters and len(letters) >= 4:
            caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
            if caps_ratio > 0.7:
                return True

        if text.count("!") >= 3 or text.count("?") >= 3:
            return True

        return False

    except Exception as e:
        logger.warning(f"is_high_emotional_intensity failed on input, defaulting False: {e}")
        return False

# ============================================================
# MODEL ROUTING
# ============================================================

def pick_chat_model(language: str, user_message: str, memory_block: str) -> str:
    """
    Decide which model handles this turn.

    Flagship triggers (any one is enough):
      - language isn't one of the top 10 most spoken
      - message reads as emotionally intense (crisis, heavy
        emotion, big wins, shouting, flat one-word replies)
      - memory context is in play (recalling patterns, goals,
        mood history) — this needs more nuance than the fast
        model reliably delivers

    Otherwise: fast model for routine small talk.

    Never raises — falls back to flagship on any internal error,
    since erring toward quality is the safer failure mode here.
    """
    try:
        language_norm = (language or "").strip().lower()

        if language_norm not in TOP_LANGUAGES:
            logger.info(f"routing -> flagship (language='{language_norm}' not in top 10)")
            return FLAGSHIP_MODEL

        if is_high_emotional_intensity(user_message):
            logger.info("routing -> flagship (high emotional intensity)")
            return FLAGSHIP_MODEL

        if memory_block:
            logger.info("routing -> flagship (memory context active)")
            return FLAGSHIP_MODEL

        logger.info(f"routing -> fast (language='{language_norm}', routine, no memory)")
        return FAST_MODEL

    except Exception as e:
        logger.error(f"pick_chat_model failed, defaulting to flagship for safety: {e}")
        return FLAGSHIP_MODEL

# ============================================================
# MEMORY CONTEXT BUILDER
# ============================================================

def build_memory_context(memories: list, context: dict, limit: int = 10) -> str:
    """
    Build structured memory block for LLM injection.
    Prioritized, capped at top `limit` (default 10), formatted cleanly.
    Defensive against malformed input — never raises.
    """
    try:
        parts = []

        memories = memories or []
        context = context or {}

        # Sort by importance descending, take top `limit`
        sorted_memories = sorted(
            memories,
            key=lambda m: m.get("importance", 1) if isinstance(m, dict) else 1,
            reverse=True
        )[:limit]

        memory_lines = []
        for m in sorted_memories:
            if not isinstance(m, dict):
                continue
            text = (m.get("memory_text") or "").strip()
            if not text:
                continue
            category = (m.get("category") or "").strip()
            memory_lines.append(f"  - [{category}] {text}" if category else f"  - {text}")
        if memory_lines:
            parts.append("USER MEMORY:")
            parts.extend(memory_lines)

        today_mood = context.get("today_mood")
        if today_mood and isinstance(today_mood, dict) and today_mood.get("mood"):
            parts.append(f"MOOD TODAY: {today_mood['mood']}")

        active_goals = context.get("active_goals") or []
        goal_lines = []
        for g in active_goals:
            if not isinstance(g, dict):
                continue
            title = (g.get("title") or "").strip()
            if title:
                goal_lines.append(f"  - {title}")
        if goal_lines:
            parts.append("ACTIVE GOALS:")
            parts.extend(goal_lines)

        return "\n".join(parts) if parts else ""

    except Exception as e:
        logger.error(f"build_memory_context failed, returning empty context: {e}")
        return ""

# ============================================================
# CLEAN HISTORY BUILDER
# ============================================================

def clean_history(raw_history: list) -> list:
    """
    Ensure history is clean role-based format only.
    Remove duplicates, fix roles, cap at last 10 messages.
    Defensive against malformed rows — skips bad entries
    instead of crashing the whole request.
    """
    valid_roles = {"user", "assistant"}
    seen = set()
    cleaned = []

    for msg in (raw_history or []):
        try:
            if not isinstance(msg, dict):
                continue

            role = msg.get("role", "")
            content = (msg.get("content") or "").strip()

            if role not in valid_roles:
                continue
            if not content:
                continue

            key = f"{role}:{content}"
            if key in seen:
                continue

            seen.add(key)
            cleaned.append({"role": role, "content": content})

        except Exception as e:
            logger.warning(f"clean_history: skipping malformed entry: {e}")
            continue

    # Keep only last 10 messages
    return cleaned[-10:]

# ============================================================
# MEMORY EXTRACTION — judgment-based, categorized
# ============================================================
# Replaces the old keyword-trigger version (matching phrases like
# "i love" or "my exam") with an actual judgment call about what a
# close friend would bother remembering -- including things with no
# fixed phrasing at all, like "I finally got the login working".
#
# Goals are deliberately NOT a category here -- they already have
# their own lifecycle in the `goals` table (see extract_goal /
# detect_goal_completion below). Recurring interests/hobbies are
# also excluded -- see interest_memory.py, a separate system with
# its own mention-count tracking. This only covers durable personal
# facts: identity, preferences, accomplishments, struggles,
# important people/pets, meaningful events, and promises/plans.

MEMORY_CATEGORIES = [
    "identity", "preference", "accomplishment", "struggle", "person", "event", "promise",
]


def extract_memory_worthy(text: str) -> tuple[str | None, str | None, int]:
    """
    Returns (memory_text, category, importance) or (None, None, 0).
    memory_text is a short, clean, rewritten line -- not a raw slice
    of the message -- so it reads naturally when surfaced back in a
    future conversation. Importance: 3 = major/identity, 2 =
    notable, 1 = minor. Never raises -- a failed extraction just
    means nothing is saved this turn.
    """
    if not text or not text.strip() or len(text.strip()) < 5:
        return None, None, 0

    try:
        response = openai_client.chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You're deciding what a genuinely close friend would "
                        "bother remembering from this message -- not every "
                        "detail, only things worth bringing up again later: "
                        "who they are (name, background, where they live/work/"
                        "study), a real preference or favorite, something they "
                        "accomplished, something they're struggling with, an "
                        "important person or pet, a meaningful event, or a "
                        "promise/plan they made. Ignore small talk, questions, "
                        "and anything forgettable.\n\n"
                        "If it's worth remembering, rewrite it as a short, "
                        "clean third-person memory line, not a copy of their "
                        "exact words -- e.g. \"Stuck on a login bug for their "
                        "app\" or \"Got the login bug working after weeks "
                        "stuck on it\" or \"Has a dog named Max\".\n\n"
                        "Reply with ONLY a JSON object, no other text:\n"
                        '{"worth_remembering": true or false, '
                        '"memory": "short clean memory line, or empty string", '
                        f'"category": "one of {MEMORY_CATEGORIES}, or empty string", '
                        '"importance": 1, 2, or 3}'
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
            return None, None, 0

        data = json.loads(content.strip())

        if not data.get("worth_remembering"):
            return None, None, 0

        memory_text = (data.get("memory") or "").strip()
        category = (data.get("category") or "").strip().lower()
        importance = data.get("importance")

        if not memory_text or len(memory_text) > 200:
            return None, None, 0
        if category not in MEMORY_CATEGORIES:
            category = None
        if importance not in (1, 2, 3):
            importance = 1

        return memory_text, category, importance

    except Exception as e:
        logger.warning(f"extract_memory_worthy failed, skipping: {e}")
        return None, None, 0


# ============================================================
# GOAL COMPLETION DETECTION
# ============================================================
# The connective tissue behind "you finally got that login working"
# -- separate from extract_goal (which only ever creates a goal),
# this checks whether a message indicates one of the person's
# EXISTING active goals was just finished, so it can be closed out
# and logged as an accomplishment rather than lingering forever as
# "still working on it".

def detect_goal_completion(active_goals: list[str], text: str) -> str | None:
    """
    Returns the exact matching title of an active goal this message
    indicates was just completed, or None. Strict by design -- only
    a clear completion matches, not general progress or a passing
    mention. Never raises.
    """
    if not text or not text.strip() or not active_goals:
        return None

    try:
        goals_list = "\n".join(f"- {g}" for g in active_goals)
        response = openai_client.chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Here are the person's current active goals:\n"
                        f"{goals_list}\n\n"
                        "Decide if their message CLEARLY indicates one of "
                        "these exact goals was just finished/accomplished "
                        "right now -- not general progress, not a passing "
                        "mention, a real completion.\n\n"
                        "Reply with ONLY a JSON object, no other text:\n"
                        '{"completed": true or false, "goal": "exact '
                        'matching title from the list above, or empty string"}'
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
        if not data.get("completed"):
            return None

        goal = (data.get("goal") or "").strip()
        return goal if goal in active_goals else None

    except Exception as e:
        logger.warning(f"detect_goal_completion failed, skipping: {e}")
        return None

# ============================================================
# MOOD DETECTION
# ============================================================
# Wires up OllieDB.update_mood / the "MOOD TODAY" prompt slot in
# build_memory_context, both of which already existed with no
# write path anywhere calling them — moods were readable but
# never actually set. Mirrors extract_interest's shape: a cheap
# fast-model call, skips on anything unclear, never raises.

def detect_mood(text: str) -> str | None:
    """
    Reads the user's current mood from their message — not
    keyword matching, so it generalizes past a fixed word list.
    Returns a short lowercase mood label (e.g. "stressed",
    "happy", "anxious"), or None if the message doesn't clearly
    convey one (small talk, questions, neutral statements, etc).
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
                        "Decide if this message clearly conveys how the "
                        "person is feeling right now — their mood, not "
                        "just the topic they're discussing. Ignore "
                        "neutral small talk, questions, or anything "
                        "where a mood isn't actually expressed.\n\n"
                        "Reply with ONLY a JSON object, no other text:\n"
                        '{"has_mood": true or false, '
                        '"mood": "one lowercase word, or empty string"}'
                    )
                },
                {"role": "user", "content": text}
            ],
            max_completion_tokens=40,
            temperature=0,
            timeout=10,
        )

        content = response.choices[0].message.content
        if not content:
            return None

        data = json.loads(content.strip())

        if not data.get("has_mood"):
            return None

        mood = (data.get("mood") or "").strip().lower()

        if not mood or len(mood) > 20 or " " in mood:
            return None

        return mood

    except Exception as e:
        logger.warning(f"detect_mood failed, skipping: {e}")
        return None

# ============================================================
# GOAL DETECTION
# ============================================================
# Wires up OllieDB.save_goal / the "ACTIVE GOALS" prompt slot in
# build_memory_context — both already existed, but nothing ever
# created a goal row. Only handles creation, not completion —
# matching a mention back to an existing goal and deciding
# whether it means "done" vs "gave up" vs "still going" is a
# fuzzier problem than detection alone, deliberately left alone
# here rather than guessed at.

def extract_goal(text: str) -> str | None:
    """
    Reads whether the message expresses a concrete personal goal
    or intention the person is actively working toward right now
    — not a vague wish, and not something already completed or
    abandoned. Returns a short goal title, or None if nothing
    like that is expressed.
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
                        "Decide if this message expresses a concrete "
                        "personal goal or intention the person is "
                        "actively working toward right now (e.g. \"my "
                        "goal is to get promoted this year\", \"I'm "
                        "trying to run a marathon\", \"I want to save "
                        "$10k\"). Ignore vague wishes, goals already "
                        "completed or abandoned, and anything that "
                        "isn't really a forward-looking personal goal.\n\n"
                        "Reply with ONLY a JSON object, no other text:\n"
                        '{"has_goal": true or false, '
                        '"goal": "short 3-8 word title, or empty string"}'
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

        if not data.get("has_goal"):
            return None

        goal = (data.get("goal") or "").strip()

        if not goal or len(goal) > 100:
            return None

        return goal

    except Exception as e:
        logger.warning(f"extract_goal failed, skipping: {e}")
        return None

# ============================================================
# MODERATION
# ============================================================
# Flags content via OpenAI's moderation endpoint. Observability
# only, by design — this never blocks or alters a reply. In
# particular it must never auto-refuse on the self-harm categories,
# since staying present through those is the whole point of the
# crisis-escalation behavior in the personality prompt; deciding
# whether/what to hard-block (e.g. sexual/minors) is a product and
# legal call for a human to make, not something to encode silently
# here. This just makes sure the data exists to make that call.

def moderate_text(text: str) -> dict | None:
    """
    Returns {"flagged": True, "categories": [...]} if OpenAI's
    moderation endpoint flags anything, else None — including on
    empty input or any API failure. Never raises: a moderation
    outage must never break the chat flow.
    """
    if not text or not text.strip():
        return None

    try:
        response = openai_client.moderations.create(
            model="omni-moderation-latest",
            input=text,
        )
        result = response.results[0]
        if not result.flagged:
            return None

        categories = result.categories.model_dump()
        flagged = [name for name, is_flagged in categories.items() if is_flagged]
        return {"flagged": True, "categories": flagged}

    except Exception as e:
        logger.warning(f"moderate_text failed, skipping: {e}")
        return None
