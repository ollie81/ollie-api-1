# AUTH — All authentication routes
# ============================================================
import hashlib
import logging
import re
import secrets
import random
import bcrypt
import jwt
import os
from twilio.rest import Client

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import (
    JWT_SECRET, JWT_ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS
)
from database import supabase
from email_service import send_otp_email

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
security = HTTPBearer()
logger = logging.getLogger("ollie.auth")

# ============================================================
# TWILIO CONFIG
# ============================================================

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_VERIFY_SERVICE_SID = os.getenv("TWILIO_VERIFY_SERVICE_SID")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ============================================================
# REQUEST MODELS
# ============================================================

class AuthRequest(BaseModel):
    phone_number: str
    password: str

class SignupOtpRequest(BaseModel):
    phone_number: str

class SignupRequest(BaseModel):
    phone_number: str
    password: str
    otp: str
    date_of_birth: str | None = None  # "YYYY-MM-DD" — optional so older app builds that don't send it yet keep working

class ForgotRequest(BaseModel):
    phone_number: str

class ResetRequest(BaseModel):
    phone_number: str
    otp: str
    new_password: str

class RefreshRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str

class FCMTokenRequest(BaseModel):
    fcm_token: str

class EmailSignupOtpRequest(BaseModel):
    email: str

class EmailSignupRequest(BaseModel):
    email: str
    password: str
    otp: str
    date_of_birth: str | None = None

class EmailAuthRequest(BaseModel):
    email: str
    password: str

class EmailForgotRequest(BaseModel):
    email: str

class EmailResetRequest(BaseModel):
    email: str
    otp: str
    new_password: str

# ============================================================
# JWT HELPERS
# ============================================================

def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def create_refresh_token() -> str:
    return secrets.token_urlsafe(64)

def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

# ============================================================
# EMAIL OTP HELPERS — email signup/reset generate and verify
# their own 6-digit code (see email_service.py), unlike phone
# which hands that entirely to Twilio Verify. Only ever the hash
# is stored, same principle as hash_refresh_token above.
# ============================================================

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))

def _generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"

def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()

def verify_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    payload = verify_access_token(credentials.credentials)
    user_id = payload.get("sub")
    result = supabase.table("users").select("*").eq("id", user_id).execute()
    if not result.data:
        raise HTTPException(status_code=401, detail="User not found")
    return result.data[0]

def _cancel_pending_deletion_if_needed(user: dict) -> bool:
    """
    Account deletion has a grace period (see database.py's
    ACCOUNT_DELETION_GRACE_DAYS) rather than happening the instant
    it's requested. Logging back in during that window is treated
    as changing your mind -- same effect as a dedicated "keep my
    account" button, without needing one: whichever of the three
    sign-in methods they used to prove it's really them is already
    enough. Deliberately doesn't block the login itself either way --
    a pending deletion isn't a reason to lock someone out of their
    own account, just a scheduled outcome they can still walk back.
    """
    if not user.get("deletion_requested_at"):
        return False
    supabase.table("users").update({"deletion_requested_at": None}).eq("id", user["id"]).execute()
    return True

# ============================================================
# PASSWORD HELPERS
# ============================================================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False

# ============================================================
# TWILIO HELPERS
# ============================================================

def send_direct_sms(to: str, body: str):
    """Send SMS directly using Twilio phone number (fallback)"""
    try:
        message = twilio_client.messages.create(
            body=body,
            from_=TWILIO_PHONE_NUMBER,
            to=to
        )
        return {"success": True, "sid": message.sid}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ============================================================
# AGE GATE
# ============================================================
# No date_of_birth is allowed through (older app builds don't
# collect one yet), but if one IS sent, it must parse and clear
# the COPPA-standard 13-year bar. A malformed value is rejected
# rather than ignored, so garbage input can't be used to slip
# past this — the only way through is a real, valid, adult-enough
# date, or no date at all.
MIN_SIGNUP_AGE_YEARS = 13

def _check_age_gate(date_of_birth: str | None) -> str | None:
    """Returns an error detail string if signup should be blocked, else None."""
    if not date_of_birth:
        return None
    try:
        dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
    except ValueError:
        return "Invalid date_of_birth format, expected YYYY-MM-DD"
    today = datetime.now(timezone.utc).date()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < MIN_SIGNUP_AGE_YEARS:
        return f"You must be at least {MIN_SIGNUP_AGE_YEARS} years old to create an account"
    return None

# ============================================================
# AUTH ROUTES
# ============================================================

@router.post("/signup/request-otp")
@limiter.limit("5/minute")
def request_signup_otp(req: SignupOtpRequest, request: Request):
    """
    Step 1 of signup: verify the phone number is real and not
    already registered, then send an OTP via Twilio Verify.
    The account itself is NOT created here — only after the OTP
    is confirmed in /signup below. This is what actually stops
    someone signing up with a phone number they don't own.
    """
    try:
        existing = supabase.table("users").select("id").eq("phone", req.phone_number).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="User already exists")

        twilio_client.verify.services(TWILIO_VERIFY_SERVICE_SID) \
            .verifications \
            .create(to=req.phone_number, channel='sms')

        return {"success": True, "message": "OTP sent via SMS"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"request_signup_otp failed for {req.phone_number}: {e}")
        raise HTTPException(status_code=500, detail="Could not send OTP, please try again")


@router.post("/signup")
@limiter.limit("5/minute")
def signup(req: SignupRequest, request: Request):
    """
    Step 2 of signup: verifies the OTP sent in step 1 before
    creating the account. A signup can no longer succeed without
    proving ownership of the phone number.
    """
    try:
        existing = supabase.table("users").select("id").eq("phone", req.phone_number).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="User already exists")

        age_gate_error = _check_age_gate(req.date_of_birth)
        if age_gate_error:
            raise HTTPException(status_code=400, detail=age_gate_error)

        verification_check = twilio_client.verify.services(TWILIO_VERIFY_SERVICE_SID) \
            .verification_checks \
            .create(to=req.phone_number, code=req.otp)

        if verification_check.status != "approved":
            raise HTTPException(status_code=400, detail="Invalid or expired OTP")

        hashed = hash_password(req.password)
        user_data = {
            "username": req.phone_number,
            "phone": req.phone_number,
            "password_hash": hashed,
        }
        if req.date_of_birth:
            user_data["date_of_birth"] = req.date_of_birth
        result = supabase.table("users").insert(user_data).execute()

        user = result.data[0]
        user_id = user["id"]

        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token()
        hashed_refresh = hash_refresh_token(refresh_token)

        supabase.table("refresh_tokens").insert({
            "user_id": user_id,
            "token_hash": hashed_refresh,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
        }).execute()

        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"signup failed for {req.phone_number}: {e}")
        raise HTTPException(status_code=500, detail="Could not create account, please try again")

@router.post("/login")
@limiter.limit("10/minute")
def login(req: AuthRequest, request: Request):
    try:
        result = supabase.table("users").select("*").eq("phone", req.phone_number).execute()
        if not result.data:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        user = result.data[0]
        if not verify_password(req.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        deletion_cancelled = _cancel_pending_deletion_if_needed(user)

        user_id = user["id"]
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token()
        hashed_refresh = hash_refresh_token(refresh_token)

        supabase.table("refresh_tokens").insert({
            "user_id": user_id,
            "token_hash": hashed_refresh,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
        }).execute()

        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "deletion_cancelled": deletion_cancelled,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"login failed for {req.phone_number}: {e}")
        raise HTTPException(status_code=500, detail="Could not log in, please try again")

@router.post("/refresh")
def refresh_token(req: RefreshRequest):
    hashed = hash_refresh_token(req.refresh_token)
    result = supabase.table("refresh_tokens").select("*").eq("token_hash", hashed).execute()

    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    token_row = result.data[0]
    expires_at = datetime.fromisoformat(token_row["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        supabase.table("refresh_tokens").delete().eq("token_hash", hashed).execute()
        raise HTTPException(status_code=401, detail="Refresh token expired")

    user_id = token_row["user_id"]
    supabase.table("refresh_tokens").delete().eq("token_hash", hashed).execute()

    new_access_token = create_access_token(user_id)
    new_refresh_token = create_refresh_token()
    new_hashed = hash_refresh_token(new_refresh_token)

    supabase.table("refresh_tokens").insert({
        "user_id": user_id,
        "token_hash": new_hashed,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
    }).execute()

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer"
    }

def cleanup_expired_refresh_tokens() -> None:
    """
    Periodic sweep for the scheduler. Expired rows otherwise only
    get deleted lazily when someone actually hits /refresh with
    them — a token that expires and is never retried again would
    sit in the table forever without this.
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        supabase.table("refresh_tokens").delete().lt("expires_at", now).execute()
    except Exception as e:
        logger.error(f"cleanup_expired_refresh_tokens failed: {e}")

@router.post("/logout")
def logout(req: LogoutRequest):
    hashed = hash_refresh_token(req.refresh_token)
    supabase.table("refresh_tokens").delete().eq("token_hash", hashed).execute()
    return {"success": True, "message": "Logged out"}

@router.get("/check/{phone_number}")
def check_user(phone_number: str):
    result = supabase.table("users").select("id").eq("phone", phone_number).execute()
    return {"exists": len(result.data) > 0}

# ============================================================
# FORGOT PASSWORD - TWILIO VERIFY API
# ============================================================

@router.post("/forgot")
@limiter.limit("3/minute")
def forgot_password(req: ForgotRequest, request: Request):
    try:
        # Check if user exists
        result = supabase.table("users").select("id").eq("phone", req.phone_number).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="User not found")

        # Send OTP via Twilio Verify
        verification = twilio_client.verify.services(TWILIO_VERIFY_SERVICE_SID) \
            .verifications \
            .create(to=req.phone_number, channel='sms')

        # Store verification SID in Supabase
        supabase.table("users").update({
            "otp_sid": verification.sid,
            "otp_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        }).eq("phone", req.phone_number).execute()

        return {"success": True, "message": "OTP sent via SMS"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"forgot_password failed for {req.phone_number}: {e}")
        raise HTTPException(status_code=500, detail="Could not send OTP, please try again")

# ============================================================
# RESET PASSWORD - TWILIO VERIFY API
# ============================================================

@router.post("/reset")
@limiter.limit("5/minute")
def reset_password(req: ResetRequest, request: Request):
    try:
        # Verify OTP with Twilio
        verification_check = twilio_client.verify.services(TWILIO_VERIFY_SERVICE_SID) \
            .verification_checks \
            .create(to=req.phone_number, code=req.otp)

        if verification_check.status != "approved":
            raise HTTPException(status_code=400, detail="Invalid OTP")

        # Get user
        result = supabase.table("users").select("*").eq("phone", req.phone_number).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="User not found")

        user = result.data[0]

        # Update password
        hashed = hash_password(req.new_password)
        supabase.table("users").update({
            "password_hash": hashed,
            "otp_sid": None,
            "otp_expires_at": None
        }).eq("phone", req.phone_number).execute()

        # Delete all refresh tokens for this user
        supabase.table("refresh_tokens").delete().eq("user_id", user["id"]).execute()

        return {"success": True, "message": "Password reset successful"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"reset_password failed for {req.phone_number}: {e}")
        raise HTTPException(status_code=500, detail="Could not reset password, please try again")

# ============================================================
# GOOGLE LOGIN
# ============================================================

class GoogleAuthRequest(BaseModel):
    id_token: str
    date_of_birth: str | None = None

@router.post("/google")
@limiter.limit("10/minute")
def google_login(req: GoogleAuthRequest, request: Request):
    try:
        info = id_token.verify_oauth2_token(
            req.id_token,
            google_requests.Request(),
            "431417738635-f3ipimjqmdldh0lfsf44f70irif9eoho.apps.googleusercontent.com"
        )
        email = info["email"]
        name = info.get("name", email)

        existing = supabase.table("users").select("*").eq("phone", email).execute()
        is_new_user = not existing.data

        # Google hands over an id_token, never a birthdate -- so a
        # brand-new account gets the same age gate email/phone signup
        # already enforce, just one round trip later: the client's
        # first call has no date_of_birth, gets told to go collect
        # one, then calls again with it. An existing user is already
        # past this gate (or predates it), so it's never re-checked
        # for them -- same "absence never blocks" rule as elsewhere.
        if is_new_user:
            if not req.date_of_birth:
                return {"success": False, "needs_date_of_birth": True}
            age_gate_error = _check_age_gate(req.date_of_birth)
            if age_gate_error:
                raise HTTPException(status_code=400, detail=age_gate_error)

        if existing.data:
            user = existing.data[0]
            deletion_cancelled = _cancel_pending_deletion_if_needed(user)
        else:
            new_user = {
                "username": name,
                "phone": email,
                "password_hash": ""
            }
            if req.date_of_birth:
                new_user["date_of_birth"] = req.date_of_birth
            result = supabase.table("users").insert(new_user).execute()
            user = result.data[0]
            deletion_cancelled = False

        user_id = user["id"]
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token()
        hashed_refresh = hash_refresh_token(refresh_token)

        supabase.table("refresh_tokens").insert({
            "user_id": user_id,
            "token_hash": hashed_refresh,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
        }).execute()

        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            # Lets the client show onboarding only the very first
            # time, not on every subsequent Google login.
            "is_new_user": is_new_user,
            "username": user.get("username"),
            "deletion_cancelled": deletion_cancelled,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"google_login failed: {e}")
        raise HTTPException(status_code=401, detail="Google sign-in failed")

# ============================================================
# EMAIL LOGIN — a third sign-in method alongside phone and Google.
# Uses its own `email` column (migration 014) rather than the
# `phone` column Google reuses for its identity key -- Google
# already writes every existing Google user's email into `phone`,
# so changing that lookup now would split returning Google users
# into duplicate accounts. get_current_user, /refresh, and /logout
# are completely unchanged and need no changes here -- they only
# ever key off the user's id, never how they signed in.
#
# Signup mirrors phone's two-step OTP shape, but since there's no
# Twilio-Verify-equivalent product for email, this app generates
# its own 6-digit code and verifies it itself (see email_service.py,
# _generate_otp_code/_hash_otp above). The pending code lives in
# email_signup_otps, NOT on a users row -- no account exists until
# the code is confirmed, so an abandoned signup attempt can never
# permanently squat someone else's email address.
# ============================================================

EMAIL_OTP_EXPIRE_MINUTES = 10


def _email_identity_taken(email: str) -> bool:
    """
    True if any existing user already has this email as their
    identity -- either in the `email` column (an email/password
    account) or in `phone` (where Google sign-in stores a verified
    email, see google_login). Without checking both, someone who
    already signed in with Google using this address could sign up
    again with email/password and silently end up with a second,
    disconnected account under the same email.

    Two separate .eq() queries rather than a single .or_() filter --
    building a raw PostgREST filter string from user input (which
    .or_() requires) isn't worth the injection surface for saving
    one round trip.
    """
    by_email = supabase.table("users").select("id").eq("email", email).execute()
    if by_email.data:
        return True
    by_phone = supabase.table("users").select("id").eq("phone", email).execute()
    return bool(by_phone.data)


@router.post("/email/signup/request-otp")
@limiter.limit("5/minute")
def request_email_signup_otp(req: EmailSignupOtpRequest, request: Request):
    email = req.email.strip().lower()
    if not _is_valid_email(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address")

    try:
        if _email_identity_taken(email):
            raise HTTPException(status_code=400, detail="User already exists")

        code = _generate_otp_code()
        if not send_otp_email(email, code, purpose="verify"):
            raise HTTPException(status_code=500, detail="Could not send verification code, please try again")

        supabase.table("email_signup_otps").upsert({
            "email": email,
            "otp_hash": _hash_otp(code),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=EMAIL_OTP_EXPIRE_MINUTES)).isoformat(),
        }).execute()

        return {"success": True, "message": "OTP sent via email"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"request_email_signup_otp failed for {email}: {e}")
        raise HTTPException(status_code=500, detail="Could not send OTP, please try again")


@router.post("/email/signup")
@limiter.limit("5/minute")
def email_signup(req: EmailSignupRequest, request: Request):
    """Step 2 of email signup: verify the staged code, then create the account."""
    email = req.email.strip().lower()
    try:
        if _email_identity_taken(email):
            raise HTTPException(status_code=400, detail="User already exists")

        age_gate_error = _check_age_gate(req.date_of_birth)
        if age_gate_error:
            raise HTTPException(status_code=400, detail=age_gate_error)

        pending = supabase.table("email_signup_otps").select("*").eq("email", email).execute()
        if not pending.data:
            raise HTTPException(status_code=400, detail="No pending verification for this email -- request a new code")

        row = pending.data[0]
        if datetime.now(timezone.utc) > datetime.fromisoformat(row["expires_at"]):
            supabase.table("email_signup_otps").delete().eq("email", email).execute()
            raise HTTPException(status_code=400, detail="Code expired, please request a new one")

        if _hash_otp(req.otp) != row["otp_hash"]:
            raise HTTPException(status_code=400, detail="Invalid code")

        supabase.table("email_signup_otps").delete().eq("email", email).execute()

        user_data = {
            "username": email,
            "email": email,
            "email_verified": True,
            "password_hash": hash_password(req.password),
        }
        if req.date_of_birth:
            user_data["date_of_birth"] = req.date_of_birth
        result = supabase.table("users").insert(user_data).execute()

        user_id = result.data[0]["id"]
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token()
        hashed_refresh = hash_refresh_token(refresh_token)

        supabase.table("refresh_tokens").insert({
            "user_id": user_id,
            "token_hash": hashed_refresh,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
        }).execute()

        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"email_signup failed for {email}: {e}")
        raise HTTPException(status_code=500, detail="Could not create account, please try again")


@router.post("/email/login")
@limiter.limit("10/minute")
def email_login(req: EmailAuthRequest, request: Request):
    email = req.email.strip().lower()
    try:
        result = supabase.table("users").select("*").eq("email", email).execute()
        if not result.data:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        user = result.data[0]
        if not verify_password(req.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        deletion_cancelled = _cancel_pending_deletion_if_needed(user)

        user_id = user["id"]
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token()
        hashed_refresh = hash_refresh_token(refresh_token)

        supabase.table("refresh_tokens").insert({
            "user_id": user_id,
            "token_hash": hashed_refresh,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
        }).execute()

        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "deletion_cancelled": deletion_cancelled,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"email_login failed for {email}: {e}")
        raise HTTPException(status_code=500, detail="Could not log in, please try again")


@router.post("/email/forgot")
@limiter.limit("3/minute")
def email_forgot_password(req: EmailForgotRequest, request: Request):
    email = req.email.strip().lower()
    try:
        result = supabase.table("users").select("id").eq("email", email).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="User not found")

        code = _generate_otp_code()
        if not send_otp_email(email, code, purpose="reset"):
            raise HTTPException(status_code=500, detail="Could not send verification code, please try again")

        supabase.table("users").update({
            "email_otp_hash": _hash_otp(code),
            "email_otp_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=EMAIL_OTP_EXPIRE_MINUTES)).isoformat(),
        }).eq("email", email).execute()

        return {"success": True, "message": "OTP sent via email"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"email_forgot_password failed for {email}: {e}")
        raise HTTPException(status_code=500, detail="Could not send OTP, please try again")


@router.post("/email/reset")
@limiter.limit("5/minute")
def email_reset_password(req: EmailResetRequest, request: Request):
    email = req.email.strip().lower()
    try:
        result = supabase.table("users").select("*").eq("email", email).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="User not found")
        user = result.data[0]

        if not user.get("email_otp_hash") or not user.get("email_otp_expires_at"):
            raise HTTPException(status_code=400, detail="No pending reset for this email -- request a new code")
        if datetime.now(timezone.utc) > datetime.fromisoformat(user["email_otp_expires_at"]):
            raise HTTPException(status_code=400, detail="Code expired, please request a new one")
        if _hash_otp(req.otp) != user["email_otp_hash"]:
            raise HTTPException(status_code=400, detail="Invalid code")

        supabase.table("users").update({
            "password_hash": hash_password(req.new_password),
            "email_otp_hash": None,
            "email_otp_expires_at": None,
        }).eq("email", email).execute()

        supabase.table("refresh_tokens").delete().eq("user_id", user["id"]).execute()

        return {"success": True, "message": "Password reset successful"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"email_reset_password failed for {email}: {e}")
        raise HTTPException(status_code=500, detail="Could not reset password, please try again")

# ============================================================
# FCM TOKEN
# ============================================================

@router.post("/fcm-token")
def save_fcm_token(
    req: FCMTokenRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        supabase.table("users").update({
            "fcm_token": req.fcm_token
        }).eq("id", current_user["id"]).execute()
        return {"success": True, "message": "FCM token saved"}
    except Exception as e:
        logger.error(f"save_fcm_token failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not save FCM token")
