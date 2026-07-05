# CHAT — Chat + Voice routes (production)
# ============================================================

import logging
import time

import requests
from openai import OpenAI
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List

from config import OPENAI_API_KEY, PAPLA_API_KEY, OLLIE_VOICE_ID, PAPLA_TTS_URL
from database import OllieDB
from memory import (
    detect_language,
    build_memory_context,
    clean_history,
    extract_memory_worthy,
    pick_chat_model,
)
from personality import OLLIE_PERSONALITY
from auth import get_current_user

logger = logging.getLogger("ollie.chat")

router = APIRouter()
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):
    message: str
    history: List[dict] = []

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
            response = openai_client.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=150,
                temperature=1,
                timeout=20,
            )

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
        session = db.start_session(user_id)
        session_id = session["id"]

        # Detect language
        language = detect_language(req.message)

        # Get memories + context
        memories = db.get_relevant_memories(user_id)
        context = db.get_user_context(user_id)
        memory_block = build_memory_context(memories, context)

        # Rebuild clean history server-side — fixes amnesia
        raw_history = db.get_recent_messages(user_id, limit=12)
        server_history = clean_history(raw_history)

        # Decide which model handles this turn — uncommon language,
        # high emotional intensity, or active memory context all
        # route to the flagship; routine common-language small talk
        # with no memory in play goes to the fast/cheap model.
        model = pick_chat_model(language, req.message, memory_block)

        # Save user message
        db.save_message(user_id, session_id, req.message, "user")
        db.increment_message_count(user_id)

        # Get response
        reply = get_ollie_response(req.message, language, server_history, memory_block, model)

        # Save Ollie reply
        db.save_message(user_id, session_id, reply, "ollie", 0.0)

        # Save memory with importance scoring
        memory_text, importance = extract_memory_worthy(req.message)
        if memory_text:
            db.save_memory(user_id, memory_text, importance=importance)

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
