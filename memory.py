# ============================================================
# MEMORY — Language detection + memory helpers (production)
# ============================================================

from openai import OpenAI
from config import OPENAI_API_KEY

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# LANGUAGE DETECTION
# ============================================================

def detect_language(text: str) -> str:
    try:
        response = openai_client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Return ONLY the language name in lowercase. One word only. Examples: english, french, kinyarwanda, swahili, arabic, spanish, korean, chinese, russian. Nothing else."
                },
                {"role": "user", "content": text}
            ],
            max_completion_tokens=5
        )
        return response.choices[0].message.content.strip().lower()
    except:
        return "english"

# ============================================================
# MEMORY CONTEXT BUILDER
# ============================================================

def build_memory_context(memories: list, context: dict) -> str:
    """
    Build structured memory block for LLM injection.
    Prioritized, capped at top 10, formatted cleanly.
    """
    parts = []

    # Sort by importance descending, take top 10
    sorted_memories = sorted(
        memories,
        key=lambda m: m.get("importance", 1),
        reverse=True
    )[:10]

    if sorted_memories:
        parts.append("USER MEMORY:")
        for m in sorted_memories:
            text = m.get("memory_text", "").strip()
            if text:
                parts.append(f"  - {text}")

    if context.get("today_mood"):
        parts.append(f"MOOD TODAY: {context['today_mood']['mood']}")

    if context.get("active_goals"):
        parts.append("ACTIVE GOALS:")
        for g in context["active_goals"]:
            title = g.get("title", "").strip()
            if title:
                parts.append(f"  - {title}")

    return "\n".join(parts) if parts else ""

# ============================================================
# CLEAN HISTORY BUILDER
# ============================================================

def clean_history(raw_history: list) -> list:
    """
    Ensure history is clean role-based format only.
    Remove duplicates, fix roles, cap at last 10 messages.
    """
    valid_roles = {"user", "assistant"}
    seen = set()
    cleaned = []

    for msg in raw_history:
        role = msg.get("role", "")
        content = msg.get("content", "").strip()

        if role not in valid_roles:
            continue
        if not content:
            continue

        key = f"{role}:{content}"
        if key in seen:
            continue

        seen.add(key)
        cleaned.append({"role": role, "content": content})

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
