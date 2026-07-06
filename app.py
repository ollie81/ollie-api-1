# ============================================================
# MAIN — FastAPI app startup
# ============================================================
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from apscheduler.schedulers.background import BackgroundScheduler

from config import ALLOWED_ORIGINS
from auth import router as auth_router
from chat import router as chat_router
from premium import router as premium_router
from notifications import router as notifications_router
from event_scheduler import run_due_notifications

# ============================================================
# SCHEDULER — checks for due event check-ins periodically
# ============================================================
background_scheduler = BackgroundScheduler()
background_scheduler.add_job(run_due_notifications, "interval", minutes=10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    background_scheduler.start()
    yield
    background_scheduler.shutdown()

# ============================================================
# APP SETUP
# ============================================================
app = FastAPI(title="Ollie API", lifespan=lifespan)

limiter = Limiter(key_func=get_remote_address)
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
app.include_router(notifications_router, prefix="/notifications")


@app.get("/")
def root():
    return {"message": "Ollie API is running 🚀"}
