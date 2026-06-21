# ============================================================
# MEMORY — Language detection + memory helpers
# ============================================================

from openai import OpenAI
from config import OPENAI_API_KEY
from database import OllieDB

openai_client = OpenAI(api_key=OPENAI_API_KEY)

def detect_language(text: str) -> str:
    """Detect language using GPT — single focused call"""
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

def build_memory_context(memories: list, context: dict) -> str:
    """Build structured memory block to inject into every prompt"""
    parts = []

    if memories:
        parts.append("[MEMORY CONTEXT — things this person has shared with you]")
        for m in memories:
            parts.append(f"- {m['memory_text']}")

    if context.get("today_mood"):
        parts.append(f"[TODAY'S MOOD] {context['today_mood']['mood']}")

    if context.get("active_goals"):
        parts.append("[ACTIVE GOALS]")
        for g in context["active_goals"]:
            parts.append(f"- {g.get('title', '')}")

    return "\n".join(parts) if parts else ""

def extract_memory_worthy(text: str) -> str | None:
    """Extract memory-worthy content from user message"""
    triggers = [
        "my name is", "i am", "i'm", "i hate", "i love", "i fear",
        "my boss", "my mom", "my dad", "my friend", "my exam",
        "i'm scared of", "i always", "i never", "my dream",
        "i want to", "my goal", "my birthday", "i work at",
        "my job", "my school", "my family", "i live in",
        "my biggest", "i struggle", "i wish", "my problem"
    ]
    text_lower = text.lower()
    for trigger in triggers:
        if trigger in text_lower:
            return text[:200]
    return None
