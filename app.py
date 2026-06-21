# ============================================================
# MAIN — FastAPI app startup
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config import ALLOWED_ORIGINS
from auth import router as auth_router
from chat import router as chat_router
from premium import router as premium_router

# ============================================================
# APP SETUP
# ============================================================

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Ollie API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# ROUTES
# ============================================================

app.include_router(auth_router, prefix="/auth")
app.include_router(chat_router)
app.include_router(premium_router, prefix="/premium")

@app.get("/")
def root():
    return {"message": "Ollie is alive"}
