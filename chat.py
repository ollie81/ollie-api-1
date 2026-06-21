# ============================================================
# CHAT — Chat + Voice routes
# ============================================================

import requests
from openai import OpenAI
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List

from config import OPENAI_API_KEY, PAPLA_API_KEY, OLLIE_VOICE_ID, PAPLA_TTS_URL
from database import OllieDB
from memory import detect_language, build_memory_context, extract_memory_worthy
from personality import OLLIE_PERSONALITY
from auth import get_current_user

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
# RESPONSE ENGINE — Fixed with server-side memory
# ============================================================

def get_ollie_response(
    user_input: str,
    language: str,
    server_history: list,
    memory_block: str
) -> str:
    try:
        # Build full input with memory
        full_input = user_input
        if memory_block:
            full_input = f"{memory_block}\n\nuser message: {user_input}"

        # Hard constraints — language enforcement added
        hard_constraints = f"""
REMINDER THIS TURN:
- Max 3 sentences
- Never start with "I"
- No bullet points
- No corporate language
- No "As an AI"
- Use [MEMORY CONTEXT] if present
- The user is writing in {language} — respond ONLY in {language}
- Never mix languages — stay 100% in {language}
- Match the user's energy
"""
        system_prompt = OLLIE_PERSONALITY + hard_constraints

        # Use server-side history — fixes amnesia
        messages = [{"role": "system", "content": system_prompt}]
        messages += server_history
        messages.append({"role": "user", "content": full_input})

        response = openai_client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=messages,
            max_completion_tokens=150,
            temperature=0.9
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        return "my bad something went wrong - try again"

# ============================================================
# CHAT ROUTE
# ============================================================

@router.post("/chat")
def chat(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    db = OllieDB()
    user_id = current_user["id"]

    if not db.can_send_message(user_id):
        raise HTTPException(status_code=429, detail="Daily limit reached")

    session = db.start_session(user_id)
    session_id = session["id"]

    # Detect language
    language = detect_language(req.message)

    # Get memories
    memories = db.get_relevant_memories(user_id)
    context = db.get_user_context(user_id)
    memory_block = build_memory_context(memories, context)

    # Rebuild history server-side — fixes amnesia on app close
    server_history = db.get_recent_messages(user_id, limit=10)

    # Save user message
    db.save_message(user_id, session_id, req.message, "user")
    db.increment_message_count(user_id)

    # Get response
    reply = get_ollie_response(req.message, language, server_history, memory_block)

    # Save Ollie reply
    db.save_message(user_id, session_id, reply, "ollie", 0.0)

    # Save memory if worth remembering
    memory = extract_memory_worthy(req.message)
    if memory:
        db.save_memory(user_id, memory, importance=2)

    return {"reply": reply, "language": language}

# ============================================================
# SPEAK ROUTE — Fixed: stream directly, no file saving
# ============================================================

@router.post("/speak")
def speak(req: SpeakRequest, current_user: dict = Depends(get_current_user)):
    db = OllieDB()
    user_id = current_user["id"]

    if not db.can_use_voice(user_id):
        raise HTTPException(status_code=429, detail="Voice limit reached")

    if not PAPLA_API_KEY or not OLLIE_VOICE_ID:
        raise HTTPException(status_code=500, detail="Voice not configured")

    try:
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
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            # Stream directly — no file saving on server
            return Response(content=response.content, media_type="audio/mpeg")
        else:
            raise HTTPException(status_code=500, detail="Voice generation failed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
