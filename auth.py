# AUTH — All authentication routes
# ============================================================
import hashlib
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
from datetime import datetime, timedelta
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import (
    JWT_SECRET, JWT_ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS
)
from database import supabase

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
security = HTTPBearer()

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

# ============================================================
# JWT HELPERS
# ============================================================

def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def create_refresh_token() -> str:
    return secrets.token_urlsafe(64)

def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

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

# ============================================================
# PASSWORD HELPERS
# ============================================================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

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
        raise HTTPException(status_code=500, detail=str(e))


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

        verification_check = twilio_client.verify.services(TWILIO_VERIFY_SERVICE_SID) \
            .verification_checks \
            .create(to=req.phone_number, code=req.otp)

        if verification_check.status != "approved":
            raise HTTPException(status_code=400, detail="Invalid or expired OTP")

        hashed = hash_password(req.password)
        result = supabase.table("users").insert({
            "username": req.phone_number,
            "phone": req.phone_number,
            "password_hash": hashed,
            "country": "RW"
        }).execute()

        user = result.data[0]
        user_id = user["id"]

        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token()
        hashed_refresh = hash_refresh_token(refresh_token)

        supabase.table("refresh_tokens").insert({
            "user_id": user_id,
            "token_hash": hashed_refresh,
            "expires_at": (datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
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
        raise HTTPException(status_code=500, detail=str(e))

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

        user_id = user["id"]
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token()
        hashed_refresh = hash_refresh_token(refresh_token)

        supabase.table("refresh_tokens").insert({
            "user_id": user_id,
            "token_hash": hashed_refresh,
            "expires_at": (datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
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
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/refresh")
def refresh_token(req: RefreshRequest):
    hashed = hash_refresh_token(req.refresh_token)
    result = supabase.table("refresh_tokens").select("*").eq("token_hash", hashed).execute()

    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    token_row = result.data[0]
    expires_at = datetime.fromisoformat(token_row["expires_at"])
    if datetime.utcnow() > expires_at:
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
        "expires_at": (datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
    }).execute()

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer"
    }

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
            "otp_expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        }).eq("phone", req.phone_number).execute()

        return {"success": True, "message": "OTP sent via SMS"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# RESET PASSWORD - TWILIO VERIFY API
# ============================================================

@router.post("/reset")
def reset_password(req: ResetRequest):
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
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# GOOGLE LOGIN
# ============================================================

class GoogleAuthRequest(BaseModel):
    id_token: str

@router.post("/google")
def google_login(req: GoogleAuthRequest):
    try:
        info = id_token.verify_oauth2_token(
            req.id_token,
            google_requests.Request(),
            "431417738635-f3ipimjqmdldh0lfsf44f70irif9eoho.apps.googleusercontent.com"
        )
        email = info["email"]
        name = info.get("name", email)

        existing = supabase.table("users").select("*").eq("phone", email).execute()
        if existing.data:
            user = existing.data[0]
        else:
            result = supabase.table("users").insert({
                "username": name,
                "phone": email,
                "password_hash": "",
                "country": "RW"
            }).execute()
            user = result.data[0]

        user_id = user["id"]
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token()
        hashed_refresh = hash_refresh_token(refresh_token)

        supabase.table("refresh_tokens").insert({
            "user_id": user_id,
            "token_hash": hashed_refresh,
            "expires_at": (datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
        }).execute()

        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

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
        raise HTTPException(status_code=500, detail=str(e))
