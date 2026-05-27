from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn

# Import everything from your existing app.py
from app import (
    get_ollie_response,
    detect_language,
    detect_emotion,
    extract_memory_worthy,
    OllieDB,
    supabase,
    OLLIE_PERSONALITY,
    openai_client
)

app = FastAPI(title="Ollie API", version="1.0.0")

# Allow Flutter to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db = OllieDB(supabase)

# ============================================================
# REQUEST AND RESPONSE MODELS
# ============================================================

class ChatRequest(BaseModel):
    username: str
    message: str
    history: Optional[list] = []
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    language: str
    emotion: str
    session_id: Optional[str] = None

class UserRequest(BaseModel):
    username: str
    email: Optional[str] = None

# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():
    return {"status": "Ollie is alive 🔥", "version": "1.0.0"}

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        # Get or create user
        user = db.get_or_create_user(request.username)
        user_id = user["id"]

        # Create session if needed
        session_id = request.session_id
        if not session_id:
            session = db.start_session(user_id)
            session_id = session["id"]

        # Detect language and emotion
        language = detect_language(request.message)
        emotion, emotion_score = detect_emotion(request.message)

        # Build history
        history = request.history or []
        history.append({
            "role": "user",
            "content": request.message
        })

        # Keep history manageable
        if len(history) > 20:
            history = history[-20:]

        # Get Ollie's response
        reply = get_ollie_response(
            request.message,
            language,
            emotion,
            history
        )

        # Save to Supabase
        db.save_message(user_id, session_id, request.message, "user", emotion_score)
        db.save_message(user_id, session_id, reply, "ollie", 0.0)

        # Save memory if worth remembering
        memory = extract_memory_worthy(request.message, reply)
        if memory:
            db.save_memory(user_id, memory, importance=2)

        # Update mood
        db.update_mood(user_id, emotion)

        return ChatResponse(
            reply=reply,
            language=language,
            emotion=emotion,
            session_id=session_id
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/user/create")
async def create_user(request: UserRequest):
    try:
        user = db.get_or_create_user(request.username, request.email)
        return {"user_id": user["id"], "username": user["username"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/{username}/memories")
async def get_memories(username: str):
    try:
        user = db.get_or_create_user(username)
        memories = db.get_relevant_memories(user["id"])
        return {"memories": memories}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/{username}/context")
async def get_context(username: str):
    try:
        user = db.get_or_create_user(username)
        context = db.get_user_context(user["id"])
        return context
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "healthy"}

# ============================================================
# RUN SERVER
# ============================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)