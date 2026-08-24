import logging
import time
from datetime import datetime, timedelta

import requests
from openai import OpenAI
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List

from config import OPENAI_API_KEY, PAPLA_API_KEY, OLLIE_VOICE_ID, PAPLA_TTS_URL
from database import OllieDB, supabase
from memory import (
    detect_language,
    build_memory_context,
    clean_history,
    extract_memory_worthy,
    detect_mood,
    extract_goal,
    pick_chat_model,
    FLAGSHIP_MODEL,
    CRISIS_KEYWORDS,
    moderate_text,
)
from personality import OLLIE_PERSONALITY
from auth import get_current_user
from event_scheduler import maybe_schedule_event, maybe_schedule_reminder
from interest_memory import maybe_track_interest, build_interest_context
logger = logging.getLogger("ollie.chat")

router = APIRouter()
openai_client = OpenAI(api_key=OPENAI_API_KEY)


@router.get("/history")
def get_history(current_user: dict = Depends(get_current_user)):
    """
    Returns this user's past messages so the chat screen can
    reload them on open, instead of starting empty every time.
    """
    db = OllieDB()
    history = db.get_conversation_history(current_user["id"], limit=50)
    return {
        "messages": [
            {
                "sender": msg["sender"],
                "message": msg["message"],
                "created_at": msg["created_at"],
            }
            for msg in history
        ]
    }

# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):
    message: str
    history: List[dict] = []
    utc_offset_minutes: int | None = None

class SpeakRequest(BaseModel):
    message: str

# ============================================================
# PROMPT BUILDER
# ============================================================

def build_system_prompt(language: str, memory_block: str) -> str:
    """
    Structure: PERSONALITY → MEMORY → LANGUAGE RULE → HARD RULES
    Memory injected before rules are finalized.
    """
    parts = [OLLIE_PERSONALITY]

    if memory_block:
        parts.append(f"\n{memory_block}")

    parts.append(f"""
LANGUAGE RULE:
The user is writing in {language}.
Respond ONLY in {language}. Never mix languages in one response.
If language is unclear, use english.

HARD RULES THIS TURN:
- Max 2 sentences
- Never start with "I"
- No bullet points
- No corporate language
- No "As an AI"
- Match the user's energy
""")

    return "\n".join(parts)

# ============================================================
# RESPONSE ENGINE
# ============================================================

def get_ollie_response(
    user_input: str,
    language: str,
    server_history: list,
    memory_block: str,
    model: str,
    max_retries: int = 2,
) -> str:
    """
    Calls the model to get Ollie's reply. Retries on transient
    errors (rate limits, timeouts, connection issues) before
    falling back to a friendly error message. Every failure is
    logged with which model and attempt number, so real outages
    are visible instead of silently producing generic replies.
    """
    system_prompt = build_system_prompt(language, memory_block)

    messages = [{"role": "system", "content": system_prompt}]
    messages += server_history
    messages.append({"role": "user", "content": user_input})

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            request_kwargs = dict(
                model=model,
                messages=messages,
                max_completion_tokens=400,
                temperature=1,
                timeout=20,
            )

            # gpt-5.5 is a reasoning model — hidden reasoning tokens
            # count against max_completion_tokens and can silently eat
            # the whole budget, leaving an empty visible reply. Keep
            # reasoning effort minimal since Ollie's replies are short
            # and conversational, not multi-step problems.
            if model == FLAGSHIP_MODEL:
                request_kwargs["reasoning_effort"] = "low"

            response = openai_client.chat.completions.create(**request_kwargs)

            content = response.choices[0].message.content
            if not content or not content.strip():
                logger.warning(f"get_ollie_response: empty content from {model} on attempt {attempt + 1}")
                if attempt < max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return "my bad, something went wrong — try again"

            return content.strip()

        except Exception as e:
            last_error = e
            logger.warning(f"get_ollie_response: {model} attempt {attempt + 1} failed: {e}")
            if attempt < max_retries:
                time.sleep(0.5 * (attempt + 1))

    logger.error(f"get_ollie_response: all retries exhausted on {model}. Last error: {last_error}")
    return "my bad something went wrong - try again"

# ============================================================
# CRISIS BACKSTOP
# ============================================================
# The personality prompt already instructs Ollie to surface a real
# crisis resource on self-harm/abuse/emergency disclosures, but
# that's not guaranteed — the model can fail to comply. This is a
# server-side backstop: guarantees a resource line ships regardless
# of what the model said, and leaves an audit trail for follow-up.

def _is_crisis_message(text: str) -> bool:
    text_lower = (text or "").lower()
    return any(kw in text_lower for kw in CRISIS_KEYWORDS)


def _flag_crisis_message(user_id: str, message: str) -> None:
    logger.warning(f"chat: crisis-keyword match for user {user_id}")
    try:
        # Requires a Supabase table "crisis_flags" with columns:
        # id (uuid, pk, default gen_random_uuid()), user_id (text),
        # message (text), created_at (timestamptz, default now()).
        # Best-effort only — safe to leave uncreated, this never
        # blocks the reply either way.
        supabase.table("crisis_flags").insert({
            "user_id": user_id,
            "message": message,
        }).execute()
    except Exception as e:
        logger.warning(f"chat: could not record crisis_flags row (table may not exist yet): {e}")


def _flag_moderation(user_id: str, direction: str, text: str, categories: list) -> None:
    logger.warning(f"chat: moderation flag ({direction}) for user {user_id}: {categories}")
    try:
        # Requires a Supabase table "moderation_flags" with columns:
        # id (uuid, pk, default gen_random_uuid()), user_id (text),
        # direction (text: 'input' | 'output'), categories (text),
        # message (text), created_at (timestamptz, default now()).
        # Best-effort only — safe to leave uncreated, this never
        # blocks the reply either way.
        supabase.table("moderation_flags").insert({
            "user_id": user_id,
            "direction": direction,
            "categories": ", ".join(categories),
            "message": text,
        }).execute()
    except Exception as e:
        logger.warning(f"chat: could not record moderation_flags row (table may not exist yet): {e}")

# ============================================================
# CHAT ROUTE
# ============================================================

@router.post("/chat")
def chat(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    db = OllieDB()
    user_id = current_user["id"]

    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if not db.can_send_message(user_id):
        raise HTTPException(status_code=429, detail="Daily limit reached")

    try:
        session_id = db.get_or_create_session(user_id)

        # Detect language
        language = detect_language(req.message)

        # Moderation — audit trail only, never blocks the reply.
        input_moderation = moderate_text(req.message)
        if input_moderation:
            _flag_moderation(user_id, "input", req.message, input_moderation["categories"])

        # Get memories + context
        memories = db.get_relevant_memories(user_id)
        context = db.get_user_context(user_id)
        memory_block = build_memory_context(memories, context)

        # Rebuild clean history server-side — fixes amnesia
        raw_history = db.get_recent_messages(user_id, limit=12)
        server_history = clean_history(raw_history)

        # Decide which model handles this turn — based on the
        # original memory block only (facts/mood/goals). Interests
        # are deliberately excluded from this decision, since they
        # populate quickly for active users and would otherwise push
        # almost every message onto the flagship model.
        model = pick_chat_model(language, req.message, memory_block)

        # Interest memory — separate, additive system. Added to the
        # PROMPT CONTEXT only, after routing is already decided, so
        # it enriches Ollie's replies without affecting cost/routing.
        interest_block = build_interest_context(user_id)
        prompt_context = f"{memory_block}\n{interest_block}" if interest_block else memory_block

        # Save user message
        db.save_message(user_id, session_id, req.message, "user")
        db.increment_message_count(user_id)

        # Get response
        reply = get_ollie_response(req.message, language, server_history, prompt_context, model)

        output_moderation = moderate_text(reply)
        if output_moderation:
            _flag_moderation(user_id, "output", reply, output_moderation["categories"])

        # Crisis backstop — guarantee a resource line on flagged
        # messages regardless of whether the model included one.
        if _is_crisis_message(req.message):
            _flag_crisis_message(user_id, req.message)
            reply = reply.rstrip() + (
                "\n\nif it ever feels like too much, please reach out to "
                "a crisis line or someone you trust — i'm not going anywhere either."
            )

        # Save Ollie reply
        db.save_message(user_id, session_id, reply, "ollie", 0.0)

        # Save memory with importance scoring
        memory_text, importance = extract_memory_worthy(req.message)
        if memory_text:
            db.save_memory(user_id, memory_text, importance=importance)

        # Update today's mood if this message clearly conveys one —
        # feeds the "MOOD TODAY" block back into tomorrow's context.
        mood = detect_mood(req.message)
        if mood:
            db.update_mood(user_id, mood)

        # Save a goal if one was clearly expressed — feeds the
        # "ACTIVE GOALS" block back into future context.
        goal = extract_goal(req.message)
        if goal:
            db.save_goal(user_id, goal)

        # Check if this message describes a meaningful future event
        # (interview, exam, first date, deadline, family event —
        # anything, not just medical) worth a genuine check-in later.
        # This only SCHEDULES a future notification — it does not send
        # one now. Only runs on already-important, non-crisis messages,
        # so it doesn't fire on every mention of a date/time.
        maybe_schedule_event(user_id, req.message, importance)

        # Explicit "remind me to X" requests — independent of the
        # importance gate above, since a reminder request may not
        # score as memory-worthy on its own.
        maybe_schedule_reminder(user_id, req.message, req.utc_offset_minutes)

        # Track ongoing interests/hobbies mentioned — separate,
        # additive system. Failure here never breaks the reply.
        maybe_track_interest(user_id, req.message)

        return {"reply": reply, "language": language, "model_used": model}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"chat route failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong, please try again")

# ============================================================
# SPEAK ROUTE — streams directly, no file saving
# ============================================================

@router.post("/speak")
def speak(req: SpeakRequest, current_user: dict = Depends(get_current_user)):
    db = OllieDB()
    user_id = current_user["id"]

    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if not db.can_use_voice(user_id):
        raise HTTPException(status_code=429, detail="Voice limit reached")

    if not PAPLA_API_KEY or not OLLIE_VOICE_ID:
        raise HTTPException(status_code=500, detail="Voice not configured")

    url = f"{PAPLA_TTS_URL}/{OLLIE_VOICE_ID}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {PAPLA_API_KEY}"
    }
    data = {
        "text": req.message,
        "model_id": "papla_p1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        }
    }

    max_retries = 1
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(url, json=data, headers=headers, timeout=15)
            if response.status_code == 200:
                return Response(content=response.content, media_type="audio/mpeg")

            logger.warning(f"speak: Papla returned {response.status_code} on attempt {attempt + 1}")
            last_error = f"Papla returned status {response.status_code}"

        except requests.RequestException as e:
            last_error = str(e)
            logger.warning(f"speak: request to Papla failed on attempt {attempt + 1}: {e}")

        if attempt < max_retries:
            time.sleep(0.5)

    logger.error(f"speak: voice generation failed for user {user_id} after retries: {last_error}")
    raise HTTPException(status_code=500, detail="Voice generation failed")
