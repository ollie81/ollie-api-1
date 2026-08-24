
# ============================================================
# MEMORY — Language detection + memory helpers (production)
# MEMORY — Language detection + memory helpers (production)
# ============================================================

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

def build_memory_context(memories: list, context: dict) -> str:
    """
    Build structured memory block for LLM injection.
    Prioritized, capped at top 10, formatted cleanly.
    Defensive against malformed input — never raises.
    """
    try:
        parts = []

        memories = memories or []
        context = context or {}

        # Sort by importance descending, take top 10
        sorted_memories = sorted(
            memories,
            key=lambda m: m.get("importance", 1) if isinstance(m, dict) else 1,
            reverse=True
        )[:10]

        if sorted_memories:
            parts.append("USER MEMORY:")
            for m in sorted_memories:
                if not isinstance(m, dict):
                    continue
                text = (m.get("memory_text") or "").strip()
                if text:
                    parts.append(f"  - {text}")

        today_mood = context.get("today_mood")
        if today_mood and isinstance(today_mood, dict) and today_mood.get("mood"):
            parts.append(f"MOOD TODAY: {today_mood['mood']}")

        active_goals = context.get("active_goals")
        if active_goals:
            parts.append("ACTIVE GOALS:")
            for g in active_goals:
                if not isinstance(g, dict):
                    continue
                title = (g.get("title") or "").strip()
                if title:
                    parts.append(f"  - {title}")

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
# MEMORY EXTRACTION — Improved scoring
# ============================================================

# High importance triggers — identity level
IDENTITY_TRIGGERS = [
    "my name is", "call me", "i am", "i'm from",
    "i live in", "i work at", "my job is", "i study",
    "my birthday is", "i was born"
]

# Medium importance triggers — preferences and emotions
PREFERENCE_TRIGGERS = [
    "i love", "i hate", "i fear", "i enjoy", "i prefer",
    "my favorite", "i always", "i never", "i believe",
    "my dream", "my goal", "i want to", "i'm scared of"
]

# Low importance triggers — situational
SITUATIONAL_TRIGGERS = [
    "my boss", "my mom", "my dad", "my friend", "my sister",
    "my brother", "my exam", "my problem", "my school",
    "my family", "i'm struggling", "i'm trying"
]

def extract_memory_worthy(text: str) -> tuple[str | None, int]:
    """
    Returns (memory_text, importance) or (None, 0).
    Importance: 3 = identity, 2 = preference, 1 = situational
    """
    try:
        if not text:
            return None, 0

        text_lower = text.lower().strip()

        if not text_lower or len(text_lower) < 5:
            return None, 0

        for trigger in IDENTITY_TRIGGERS:
            if trigger in text_lower:
                return text[:200], 3

        for trigger in PREFERENCE_TRIGGERS:
            if trigger in text_lower:
                return text[:200], 2

        for trigger in SITUATIONAL_TRIGGERS:
            if trigger in text_lower:
                return text[:200], 1

        return None, 0

    except Exception as e:
        logger.warning(f"extract_memory_worthy failed, skipping: {e}")
        return None, 0

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
