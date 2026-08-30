import base64
import logging
import time
from datetime import datetime, timedelta, timezone

from openai import OpenAI
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Request
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import OPENAI_API_KEY
from database import OllieDB, supabase, estimate_speech_seconds, VOICE_INPUT_TRIAL_COST_SECONDS
from premium import is_premium_active
from memory import (
    detect_language,
    build_memory_context,
    clean_history,
    extract_memory_worthy,
    detect_mood,
    extract_goal,
    detect_goal_completion,
    pick_chat_model,
    FAST_MODEL,
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
limiter = Limiter(key_func=get_remote_address)


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

def _current_time_line(utc_offset_minutes: int | None) -> str:
    """
    Ollie previously had zero notion of the current time or date
    — asked "what time is it", the model had nothing to go on and
    could only guess or deflect. This gives it the real answer,
    in the user's own local time when known (same utc_offset_minutes
    already sent for reminder scheduling).
    """
    now_utc = datetime.now(timezone.utc)
    if utc_offset_minutes is not None:
        local_dt = now_utc + timedelta(minutes=utc_offset_minutes)
        return (
            f"CURRENT TIME: it's {local_dt.strftime('%A, %B %d, %Y, %I:%M %p').replace(' 0', ' ')} "
            "where the user is right now. If asked what time or day it is, just answer — "
            "never say you don't know or can't tell."
        )
    return (
        f"CURRENT TIME (UTC — user's local timezone wasn't sent this turn): "
        f"{now_utc.strftime('%A, %B %d, %Y, %I:%M %p').replace(' 0', ' ')} UTC."
    )


def _location_block(current_user: dict) -> str:
    """
    Lets Ollie sound like a local -- culture, food, sports, slang,
    holidays, current local context -- instead of defaulting to
    generic/US-centric references. Entirely opt-in: current_user is
    already the full row (get_current_user selects "*"), so reading
    country/region/district here is free, no extra query -- and
    empty for anyone who's never set these in Settings, same as
    memory context being empty for a brand-new user.
    """
    place = ", ".join(
        p.strip() for p in (
            current_user.get("district"),
            current_user.get("region"),
            current_user.get("country"),
        ) if p and p.strip()
    )
    if not place:
        return ""
    return (
        f"\nUSER'S LOCATION: {place}. Use this to sound like a local "
        "when it's natural -- local culture, food, sports, slang, "
        "holidays, weather, current local context. Never announce "
        "that you know their location or make it feel like "
        "surveillance -- just BE from-here in how you talk, the same "
        "way memory should feel natural, never creepy."
    )


def build_system_prompt(
    language: str,
    memory_block: str,
    utc_offset_minutes: int | None = None,
    location_block: str = "",
) -> str:
    """
    Structure: PERSONALITY → MEMORY → LOCATION → TIME → LANGUAGE RULE → HARD RULES
    Memory injected before rules are finalized.
    """
    parts = [OLLIE_PERSONALITY]

    if memory_block:
        parts.append(f"\n{memory_block}")

    if location_block:
        parts.append(location_block)

    parts.append(f"\n{_current_time_line(utc_offset_minutes)}")

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
    utc_offset_minutes: int | None = None,
    max_retries: int = 2,
    location_block: str = "",
) -> str:
    """
    Calls the model to get Ollie's reply. Retries on transient
    errors (rate limits, timeouts, connection issues) before
    falling back to a friendly error message. Every failure is
    logged with which model and attempt number, so real outages
    are visible instead of silently producing generic replies.
    """
    system_prompt = build_system_prompt(language, memory_block, utc_offset_minutes, location_block)

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
# CHAT PIPELINE — shared by the text (/chat) and voice
# (/chat/voice) entry points. Callers own their own entry
# validation (empty-message checks, daily limits, premium
# gating) before calling this.
# ============================================================

def _process_chat_message(db: OllieDB, user_id: str, message: str, utc_offset_minutes: int | None, current_user: dict) -> dict:
    session_id = db.get_or_create_session(user_id)
    db.remember_utc_offset(user_id, utc_offset_minutes)

    # Detect language
    language = detect_language(message)

    # Moderation — audit trail only, never blocks the reply.
    input_moderation = moderate_text(message)
    if input_moderation:
        _flag_moderation(user_id, "input", message, input_moderation["categories"])

    # Get memories + context — retrieval is skipped entirely when
    # the user has turned memory off in Settings, so Ollie stops
    # using previously stored memories immediately, not just stops
    # adding new ones.
    memory_enabled = current_user.get("memory_enabled") is not False
    if memory_enabled:
        memories = db.get_relevant_memories(user_id)
        context = db.get_user_context(user_id)
        memory_block = build_memory_context(memories, context)
    else:
        context = {}
        memory_block = ""

    # Rebuild clean history server-side — fixes amnesia
    raw_history = db.get_recent_messages(user_id, limit=12)
    server_history = clean_history(raw_history)

    # Decide which model handles this turn — based on the
    # original memory block only (facts/mood/goals). Interests
    # are deliberately excluded from this decision, since they
    # populate quickly for active users and would otherwise push
    # almost every message onto the flagship model.
    model = pick_chat_model(language, message, memory_block)

    # Interest memory — separate, additive system. Added to the
    # PROMPT CONTEXT only, after routing is already decided, so
    # it enriches Ollie's replies without affecting cost/routing.
    # Also respects the memory toggle -- still "Ollie learning
    # things about you" in spirit.
    interest_block = build_interest_context(user_id) if memory_enabled else ""
    prompt_context = f"{memory_block}\n{interest_block}" if interest_block else memory_block

    # Save user message
    db.save_message(user_id, session_id, message, "user")

    # Get response
    location_block = _location_block(current_user)
    reply = get_ollie_response(
        message, language, server_history, prompt_context, model, utc_offset_minutes,
        location_block=location_block,
    )

    output_moderation = moderate_text(reply)
    if output_moderation:
        _flag_moderation(user_id, "output", reply, output_moderation["categories"])

    # Crisis backstop — guarantee a resource line on flagged
    # messages regardless of whether the model included one.
    if _is_crisis_message(message):
        _flag_crisis_message(user_id, message)
        reply = reply.rstrip() + (
            "\n\nif it ever feels like too much, please reach out to "
            "a crisis line or someone you trust — i'm not going anywhere either."
        )

    # Save Ollie reply
    db.save_message(user_id, session_id, reply, "ollie", 0.0)

    # Save memory with category + importance scoring. The extraction
    # call itself always runs, even with memory disabled, since
    # maybe_schedule_event below reuses its importance score for an
    # unrelated purpose (deciding whether a mentioned future event
    # deserves a check-in reminder) -- only the actual STORE is
    # gated on memory_enabled, so disabling memory stops Ollie from
    # remembering personal facts/goals/mood without silently
    # breaking reminders.
    memory_text, category, importance = extract_memory_worthy(message)
    if memory_text and memory_enabled:
        db.save_memory(user_id, memory_text, importance=importance, category=category)

    if memory_enabled:
        # Update today's mood if this message clearly conveys one —
        # feeds the "MOOD TODAY" block back into tomorrow's context.
        mood = detect_mood(message)
        if mood:
            db.update_mood(user_id, mood)

        # Save a goal if one was clearly expressed — feeds the
        # "ACTIVE GOALS" block back into future context.
        goal = extract_goal(message)
        if goal:
            db.save_goal(user_id, goal)

        # Check if this message indicates an EXISTING active goal
        # was just finished -- the connective tissue that lets Ollie
        # notice "you finally got that login working" instead of
        # just filing it as a new, unrelated memory. Closes the goal
        # out and logs a clean accomplishment memory in one step.
        active_goal_titles = [
            g.get("title") for g in context.get("active_goals", [])
            if isinstance(g, dict) and g.get("title")
        ]
        completed_goal = detect_goal_completion(active_goal_titles, message)
        if completed_goal:
            db.complete_goal(user_id, completed_goal)
            db.save_memory(user_id, f"Accomplished: {completed_goal}", importance=3, category="accomplishment")

    # Check if this message describes a meaningful future event
    # (interview, exam, first date, deadline, family event —
    # anything, not just medical) worth a genuine check-in later.
    # This only SCHEDULES a future notification — it does not send
    # one now. Only runs on already-important, non-crisis messages,
    # so it doesn't fire on every mention of a date/time.
    maybe_schedule_event(user_id, message, importance)

    # Explicit "remind me to X" requests — independent of the
    # importance gate above, since a reminder request may not
    # score as memory-worthy on its own.
    maybe_schedule_reminder(user_id, message, utc_offset_minutes)

    # Track ongoing interests/hobbies mentioned — separate,
    # additive system. Failure here never breaks the reply. Also
    # respects the memory toggle above, since it's still "Ollie
    # learning things about you" in spirit.
    if memory_enabled:
        maybe_track_interest(user_id, message)

    # Daily streak — credits at most once per local calendar day,
    # so this is safe to call on every message.
    streak = db.update_streak(user_id, utc_offset_minutes)

    return {"reply": reply, "language": language, "model_used": model, "streak": streak}

# ============================================================
# CHAT ROUTE
# ============================================================

@router.post("/chat")
def chat(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    db = OllieDB()
    user_id = current_user["id"]

    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Premium bypasses the daily cap entirely (the app's own upsell
    # copy already promises unlimited messages) but still gets
    # tracked for the informational "messages used today" display in
    # Settings -- that's never enforced against, so a plain
    # increment is fine there. Free-tier goes through
    # try_consume_message instead of a separate check-then-increment
    # (what this used to be): doing those as two steps lets
    # concurrent requests both read the same count and both pass,
    # since neither sees the other's increment yet.
    if is_premium_active(user_id):
        db.increment_message_count(user_id)
    elif not db.try_consume_message(user_id):
        raise HTTPException(status_code=429, detail="Daily limit reached")

    try:
        return _process_chat_message(db, user_id, req.message, req.utc_offset_minutes, current_user)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"chat route failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong, please try again")

# ============================================================
# VOICE CHAT ROUTE — record a voice message, get a real spoken-
# to-text-to-Ollie reply back. Unlimited for premium (see
# is_premium_active in premium.py); everyone else gets the same
# free voice trial /speak uses (TRIAL_VOICE_SECONDS_LIMIT, shared
# budget), charged a flat cost per turn since voice costs real
# money per use (Whisper + the chat model), unlike text.
# ============================================================

@router.post("/chat/voice")
async def chat_voice(
    audio: UploadFile = File(...),
    utc_offset_minutes: int | None = Form(None),
    current_user: dict = Depends(get_current_user),
):
    db = OllieDB()
    user_id = current_user["id"]
    is_premium = is_premium_active(user_id)

    try:
        audio_bytes = await audio.read()
    except Exception as e:
        logger.warning(f"chat_voice: failed to read upload for user {user_id}: {e}")
        raise HTTPException(status_code=400, detail="Could not read audio file")

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # Gated here, before the Whisper call -- the first real money
    # this turn spends -- rather than after transcription, which
    # would mean paying for Whisper on a turn that was never going
    # to be allowed through anyway. Wrapped so a DB hiccup fails
    # closed with a clean, JSON error instead of an unhandled
    # exception -- which Starlette's default handler turns into a
    # plain-text response the client can't parse a message out of.
    if not is_premium:
        try:
            trial_ok = db.try_consume_voice_trial(user_id, VOICE_INPUT_TRIAL_COST_SECONDS)
        except Exception as e:
            logger.error(f"chat_voice: trial check failed for user {user_id}: {e}")
            raise HTTPException(status_code=500, detail="Could not check your voice trial, please try again")
        if not trial_ok:
            raise HTTPException(status_code=402, detail="Voice chat requires Ollie Premium")

    try:
        transcription = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=(audio.filename or "voice.m4a", audio_bytes),
        )
        transcribed_text = (transcription.text or "").strip()
    except Exception as e:
        logger.error(f"chat_voice: transcription failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not transcribe audio, please try again")

    if not transcribed_text:
        raise HTTPException(status_code=400, detail="Couldn't hear anything in that recording")

    # Tracked the same way the text route tracks usage: informational
    # only, for the "messages used today" display -- never enforced
    # here, voice is gated by premium/trial above, not this count.
    db.increment_message_count(user_id)

    try:
        result = _process_chat_message(db, user_id, transcribed_text, utc_offset_minutes, current_user)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"chat_voice route failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong, please try again")

    result["transcribed_text"] = transcribed_text
    if not is_premium:
        # Best-effort -- the reply above is already fully processed
        # and saved (conversation history, memory, streak, everything
        # committed), so a hiccup reading the trial balance here must
        # never turn an otherwise-successful reply into a hard
        # failure for the client. Just omitted on failure, same as
        # it already is for premium users.
        try:
            result["voice_trial_seconds_remaining"] = db.get_voice_trial_remaining(user_id)
        except Exception as e:
            logger.warning(f"chat_voice: could not read voice trial balance for user {user_id}: {e}")
    return result

# ============================================================
# IMAGE CHAT ROUTE — send a photo, get a real reaction to it.
# Its own, smaller pipeline (not _process_chat_message): most of
# that pipeline -- language detection, memory/mood/goal
# extraction, event/reminder scheduling -- is built around text
# and doesn't map onto "a photo was shared". Reactions are still
# generated, saved to history, and moderated the same way; nothing
# beyond that runs. Free tier, same daily cap as text (see /chat)
# -- a shared photo isn't a premium-only moment any more than a
# shared sentence is.
# ============================================================

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB -- generous for a phone photo, caps abuse/cost

IMAGE_REACTION_INSTRUCTIONS = (
    "\n\nThe user just sent a PHOTO instead of text. React to what's "
    "actually in the image like a real friend would -- specific to what "
    "you see, not a generic \"nice pic\". If they added a caption, "
    "respond to that too, naturally."
)


def _get_image_reaction(
    image_bytes: bytes,
    content_type: str,
    caption: str | None,
    system_prompt: str,
    server_history: list,
    max_retries: int = 1,
) -> str:
    """
    Same retry-with-backoff shape as get_ollie_response -- a
    transient failure falls back to an in-character line instead
    of a hard error, so a shared photo never ends in a raw 500.
    """
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    user_content = [
        {
            "type": "text",
            "text": caption.strip() if caption and caption.strip() else "(no caption, just the photo)",
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:{content_type};base64,{b64_image}"},
        },
    ]
    messages = [{"role": "system", "content": system_prompt}] + server_history + [
        {"role": "user", "content": user_content}
    ]

    for attempt in range(max_retries + 1):
        try:
            response = openai_client.chat.completions.create(
                model=FAST_MODEL,
                messages=messages,
                max_completion_tokens=400,
                temperature=1,
                timeout=25,
            )
            content = response.choices[0].message.content
            if content and content.strip():
                return content.strip()
            logger.warning(f"_get_image_reaction: empty content on attempt {attempt + 1}")
        except Exception as e:
            logger.warning(f"_get_image_reaction: attempt {attempt + 1} failed: {e}")

        if attempt < max_retries:
            time.sleep(0.5)

    return "okay i can tell you sent something but my brain glitched — send it again?"


def _process_image_message(
    db: OllieDB,
    user_id: str,
    image_bytes: bytes,
    content_type: str,
    caption: str | None,
    utc_offset_minutes: int | None,
    current_user: dict,
) -> dict:
    session_id = db.get_or_create_session(user_id)
    db.remember_utc_offset(user_id, utc_offset_minutes)

    language = detect_language(caption) if caption and caption.strip() else "english"

    # Same memory-toggle respect as the text pipeline -- see
    # _process_chat_message.
    if current_user.get("memory_enabled") is not False:
        memories = db.get_relevant_memories(user_id)
        context = db.get_user_context(user_id)
        memory_block = build_memory_context(memories, context)
    else:
        memory_block = ""

    raw_history = db.get_recent_messages(user_id, limit=12)
    server_history = clean_history(raw_history)

    location_block = _location_block(current_user)
    system_prompt = build_system_prompt(language, memory_block, utc_offset_minutes, location_block) + IMAGE_REACTION_INSTRUCTIONS

    # The image itself is never stored -- only a text placeholder,
    # same principle as /chat/voice only keeping the transcript,
    # not the audio. Ollie's reply is what actually needs to persist.
    user_display_text = f"[shared a photo] {caption.strip()}" if caption and caption.strip() else "[shared a photo]"
    db.save_message(user_id, session_id, user_display_text, "user")

    reply = _get_image_reaction(image_bytes, content_type, caption, system_prompt, server_history)

    output_moderation = moderate_text(reply)
    if output_moderation:
        _flag_moderation(user_id, "output", reply, output_moderation["categories"])

    db.save_message(user_id, session_id, reply, "ollie", 0.0)

    streak = db.update_streak(user_id, utc_offset_minutes)

    return {"reply": reply, "streak": streak}


@router.post("/chat/image")
async def chat_image(
    image: UploadFile = File(...),
    caption: str | None = Form(None),
    utc_offset_minutes: int | None = Form(None),
    current_user: dict = Depends(get_current_user),
):
    db = OllieDB()
    user_id = current_user["id"]

    try:
        image_bytes = await image.read()
    except Exception as e:
        logger.warning(f"chat_image: failed to read upload for user {user_id}: {e}")
        raise HTTPException(status_code=400, detail="Could not read image file")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image file")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image is too large")

    content_type = image.content_type or "image/jpeg"
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    # Same free-tier gating as text chat -- a shared photo counts
    # as a message like any other, not a separate premium feature.
    if is_premium_active(user_id):
        db.increment_message_count(user_id)
    elif not db.try_consume_message(user_id):
        raise HTTPException(status_code=429, detail="Daily limit reached")

    try:
        return _process_image_message(db, user_id, image_bytes, content_type, caption, utc_offset_minutes, current_user)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"chat_image route failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong, please try again")

# ============================================================
# SPEAK ROUTE — streams directly, no file saving
# ============================================================
# Uses OpenAI TTS (same OPENAI_API_KEY already used for chat and
# Whisper transcription -- no separate voice-provider account or
# API key needed). Previously called Papla Media, which shut down;
# swapping the provider only ever touches this one function, since
# every caller (/speak, /speak/preview, and the trial/premium
# logic around them) just deals in raw audio bytes.

# One of OpenAI's fixed preset voices -- change this one line to
# try a different voice for Ollie (alternatives: alloy, echo,
# fable, nova, shimmer).
OLLIE_TTS_VOICE = "echo"


def _synthesize_speech(text: str) -> bytes:
    """
    Calls OpenAI TTS with retry and returns the raw audio bytes.
    Raises HTTPException(500) itself on failure, so callers don't
    need their own error handling around this.
    """
    max_retries = 1
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = openai_client.audio.speech.create(
                model="tts-1",
                voice=OLLIE_TTS_VOICE,
                input=text,
            )
            return response.content

        except Exception as e:
            last_error = str(e)
            logger.warning(f"_synthesize_speech: OpenAI TTS request failed on attempt {attempt + 1}: {e}")

        if attempt < max_retries:
            time.sleep(0.5)

    logger.error(f"_synthesize_speech: voice generation failed after retries: {last_error}")
    raise HTTPException(status_code=500, detail="Voice generation failed")


@router.post("/speak")
def speak(req: SpeakRequest, current_user: dict = Depends(get_current_user)):
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Voice costs real money per use (Papla TTS). Premium is
    # unlimited; everyone else gets a one-time ~60-second trial
    # (across however many messages they tap the speaker icon on)
    # so they can hear Ollie speak real replies before deciding,
    # then it's premium-only.
    db = OllieDB()
    user_id = current_user["id"]
    is_premium = is_premium_active(user_id)
    if not is_premium:
        try:
            trial_ok = db.try_consume_voice_trial(user_id, estimate_speech_seconds(req.message))
        except Exception as e:
            logger.error(f"speak: trial check failed for user {user_id}: {e}")
            raise HTTPException(status_code=500, detail="Could not check your voice trial, please try again")
        if not trial_ok:
            raise HTTPException(status_code=402, detail="Voice replies require Ollie Premium")

    audio = _synthesize_speech(req.message)

    # Lets the client show a live "X seconds left" indicator without
    # a separate round-trip -- headers ride along with the binary
    # audio body for free. Omitted for premium (nothing to count),
    # and best-effort for the trial -- audio is already synthesized
    # and paid for by this point, so a hiccup reading the balance
    # must never turn an otherwise-successful reply into a hard
    # failure for the client.
    headers = {}
    if not is_premium:
        try:
            headers["X-Voice-Trial-Remaining-Seconds"] = str(db.get_voice_trial_remaining(user_id))
        except Exception as e:
            logger.warning(f"speak: could not read voice trial balance for user {user_id}: {e}")

    return Response(content=audio, media_type="audio/mpeg", headers=headers)


# ============================================================
# VOICE PREVIEW — a short, free, fixed sample of Ollie's voice,
# so someone can hear what they'd be paying for before deciding.
# Deliberately NOT user-controllable text (always this one line)
# and rate-limited, so it can't be used as a free-TTS workaround.
# ============================================================

VOICE_PREVIEW_TEXT = "hey — it's ollie. this is what i actually sound like."


@router.post("/speak/preview")
@limiter.limit("3/day")
def speak_preview(request: Request, current_user: dict = Depends(get_current_user)):
    audio = _synthesize_speech(VOICE_PREVIEW_TEXT)
    return Response(content=audio, media_type="audio/mpeg")
