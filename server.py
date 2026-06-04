import asyncio
import base64
import json
import os
import tempfile
from datetime import datetime

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from openai import OpenAI

# Import everything from your existing Ollie brain
from ollie import (
    OllieDB,
    OLLIE_PERSONALITY,
    build_memory_context,
    detect_language,
    detect_emotion_from_llm,
    extract_memory_worthy,
    get_ollie_response,
    speak_as_ollie,
    can_send_message,
    can_use_voice,
    increment_message_count,
    supabase,
    openai_client,
    PAPLA_API_KEY,
    OLLIE_VOICE_ID,
)

load_dotenv()

app = FastAPI()

# ============================================================
# SPEECH TO TEXT
# ============================================================

def transcribe_audio(audio_bytes: bytes) -> str:
    """Convert voice audio to text using OpenAI Whisper"""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            f.flush()
            with open(f.name, "rb") as audio_file:
                transcript = openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=None  # Auto detect language
                )
            return transcript.text.strip()
    except Exception as e:
        print(f"Transcription error: {e}")
        return ""

# ============================================================
# VOICE RESPONSE
# ============================================================

def generate_voice(text: str, user_id: str, db: OllieDB) -> bytes | None:
    """Generate voice audio from Ollie's text response"""
    try:
        if not PAPLA_API_KEY or not OLLIE_VOICE_ID:
            return None

        if not can_use_voice(user_id):
            return None

        url = f"https://api.papla.media/v1/text-to-speech/{OLLIE_VOICE_ID}"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {PAPLA_API_KEY}"
        }
        data = {
            "text": text,
            "model_id": "papla_p1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            }
        }
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            return response.content
        return None
    except Exception as e:
        print(f"Voice generation error: {e}")
        return None

# ============================================================
# WEBSOCKET ENDPOINT
# ============================================================

@app.websocket("/ollie/voice/{phone_number}")
async def voice_chat(websocket: WebSocket, phone_number: str):
    await websocket.accept()

    db = OllieDB(supabase)

    # Get or create user
    user = db.get_or_create_user(username=phone_number, phone=phone_number)
    user_id = user["id"]

    # Start session
    session = db.start_session(user_id)
    session_id = session["id"]
    session_start = datetime.now()

    # Load memories
    memories = db.get_relevant_memories(user_id)
    context = db.get_user_context(user_id)
    memory_block = build_memory_context(memories, context)

    # Conversation history
    history = []
    message_count = 0
    audio_buffer = bytearray()

    # Send opening greeting
    is_returning = len(context["recent_messages"]) > 0
    greeting_prompt = f"welcome back like an old friend" if is_returning else "greet this person warmly for the first time"
    if memory_block:
        greeting_prompt = f"{memory_block}\n\n{greeting_prompt}"

    try:
        opening = openai_client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=[
                {"role": "system", "content": OLLIE_PERSONALITY},
                {"role": "user", "content": greeting_prompt}
            ],
            max_tokens=100
        )
        greeting_text = opening.choices[0].message.content.strip()

        # Send greeting text to Flutter
        await websocket.send_text(json.dumps({
            "type": "final",
            "text": greeting_text
        }))

        # Send greeting as voice
        voice_bytes = await asyncio.get_event_loop().run_in_executor(
            None, generate_voice, greeting_text, user_id, db
        )
        if voice_bytes:
            await websocket.send_text(json.dumps({
                "type": "audio",
                "audio": base64.b64encode(voice_bytes).decode("utf-8")
            }))

    except Exception as e:
        print(f"Greeting error: {e}")

    # ============================================================
    # MAIN LOOP — Listen for audio or messages
    # ============================================================

    try:
        while True:
            message = await websocket.receive()

            # Audio bytes coming in from Flutter
            if "bytes" in message:
                audio_buffer.extend(message["bytes"])

            # Text/control messages from Flutter
            elif "text" in message:
                data = json.loads(message["text"])

                # User finished speaking — process the buffered audio
                if data.get("type") == "end":
                    if not audio_buffer:
                        continue

                    # Check message limit
                    if not can_send_message(user_id):
                        await websocket.send_text(json.dumps({
                            "type": "final",
                            "text": "we've been talking a lot today 😄 come back tomorrow"
                        }))
                        audio_buffer.clear()
                        continue

                    # Transcribe audio to text
                    audio_bytes = bytes(audio_buffer)
                    audio_buffer.clear()

                    # Send partial indicator so Flutter shows "listening..."
                    await websocket.send_text(json.dumps({
                        "type": "partial",
                        "text": "..."
                    }))

                    # Run transcription in background thread
                    user_text = await asyncio.get_event_loop().run_in_executor(
                        None, transcribe_audio, audio_bytes
                    )

                    if not user_text:
                        continue

                    # Send transcription back to Flutter
                    await websocket.send_text(json.dumps({
                        "type": "partial",
                        "text": user_text
                    }))

                    # Detect language and emotion
                    language = await asyncio.get_event_loop().run_in_executor(
                        None, detect_language, user_text
                    )
                    emotion, emotion_score = await asyncio.get_event_loop().run_in_executor(
                        None, detect_emotion_from_llm, user_text
                    )

                    # Refresh memories every turn
                    memories = await asyncio.get_event_loop().run_in_executor(
                        None, db.get_relevant_memories, user_id
                    )
                    context = await asyncio.get_event_loop().run_in_executor(
                        None, db.get_user_context, user_id
                    )
                    memory_block = build_memory_context(memories, context)

                    # Save user message
                    await asyncio.get_event_loop().run_in_executor(
                        None, db.save_message,
                        user_id, session_id, user_text, "user", emotion_score
                    )
                    await asyncio.get_event_loop().run_in_executor(
                        None, increment_message_count, user_id
                    )
                    message_count += 1

                    # Add to history
                    history.append({"role": "user", "content": user_text})
                    if len(history) > 20:
                        history = history[-20:]

                    # Get Ollie's reply
                    reply = await asyncio.get_event_loop().run_in_executor(
                        None, get_ollie_response,
                        user_text, language, history, memory_block
                    )

                    # Save Ollie's reply
                    await asyncio.get_event_loop().run_in_executor(
                        None, db.save_message,
                        user_id, session_id, reply, "ollie", 0.0
                    )
                    history.append({"role": "assistant", "content": reply})

                    # Send reply text to Flutter
                    await websocket.send_text(json.dumps({
                        "type": "final",
                        "text": reply
                    }))

                    # Generate and send voice
                    voice_bytes = await asyncio.get_event_loop().run_in_executor(
                        None, generate_voice, reply, user_id, db
                    )
                    if voice_bytes:
                        await websocket.send_text(json.dumps({
                            "type": "audio",
                            "audio": base64.b64encode(voice_bytes).decode("utf-8")
                        }))

                    # Save memory if worth remembering
                    memory = extract_memory_worthy(user_text, reply)
                    if memory:
                        await asyncio.get_event_loop().run_in_executor(
                            None, db.save_memory, user_id, memory, 2
                        )

                    # Update mood
                    await asyncio.get_event_loop().run_in_executor(
                        None, db.update_mood, user_id, emotion
                    )

    except WebSocketDisconnect:
        # Clean up session on disconnect
        duration = int((datetime.now() - session_start).total_seconds() / 60)
        db.end_session(session_id, message_count, duration)
        print(f"User {phone_number} disconnected")

    except Exception as e:
        print(f"WebSocket error: {e}")
        duration = int((datetime.now() - session_start).total_seconds() / 60)
        db.end_session(session_id, message_count, duration)

# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
async def health_check():
    return {"status": "Ollie is alive 🔥"}