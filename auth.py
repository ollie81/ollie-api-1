# ============================================================
# AUTH — All authentication routes
# ============================================================

import hashlib
import secrets
import random
import bcrypt
import jwt

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
# REQUEST MODELS
# ============================================================

class AuthRequest(BaseModel):
    phone_number: str
    password: str

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
# AUTH ROUTES
# ============================================================

@router.post("/signup")
@limiter.limit("5/minute")
def signup(req: AuthRequest, request: Request):
    try:
        existing = supabase.table("users").select("id").eq("phone", req.phone_number).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="User already exists")

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

@router.post("/forgot")
@limiter.limit("3/minute")
def forgot_password(req: ForgotRequest, request: Request):
    otp = str(random.randint(100000, 999999))
    otp_expires = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    supabase.table("users").update({
        "otp": otp,
        "otp_expires_at": otp_expires
    }).eq("phone", req.phone_number).execute()
    return {"success": True, "otp": otp}

@router.post("/reset")
def reset_password(req: ResetRequest):
    result = supabase.table("users").select("*") \
        .eq("phone", req.phone_number) \
        .eq("otp", req.otp) \
        .execute()
    if not result.data:
        raise HTTPException(status_code=400, detail="Invalid OTP")

    user = result.data[0]
    otp_expires = user.get("otp_expires_at")
    if otp_expires:
        if datetime.utcnow() > datetime.fromisoformat(otp_expires):
            raise HTTPException(status_code=400, detail="OTP expired")

    hashed = hash_password(req.new_password)
    supabase.table("users").update({
        "password_hash": hashed,
        "otp": None,
        "otp_expires_at": None
    }).eq("phone", req.phone_number).execute()

    supabase.table("refresh_tokens").delete().eq("user_id", user["id"]).execute()
    return {"success": True}

class GoogleAuthRequest(BaseModel):
    id_token: str

@router.post("/google")
def google_login(req: GoogleAuthRequest):
    try:
        info = id_token.verify_oauth2_token(
            req.id_token,
            google_requests.Request(),
            "762080204480-pi9vflsb9klhgcggkjcuid214uhaa45q.apps.googleusercontent.com"
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
