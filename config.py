# ============================================================
# CONFIG — All environment variables and settings
# ============================================================

import os
import secrets
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# OPENAI
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ============================================================
# SUPABASE
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ============================================================
# JWT — Fixed: no fallback random secret
# ============================================================

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise Exception("JWT_SECRET environment variable is not set. Add it to Railway variables.")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 30

# ============================================================
# PAPLA MEDIA VOICE
# ============================================================

PAPLA_API_KEY = os.getenv("PAPLA_API_KEY")
OLLIE_VOICE_ID = os.getenv("PAPLA_VOICE_ID")
PAPLA_TTS_URL = "https://api.papla.media/v1/text-to-speech"

# ============================================================
# GOOGLE PLAY BILLING (real purchase verification)
# ============================================================

GOOGLE_PLAY_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON")
ANDROID_PACKAGE_NAME = os.getenv("ANDROID_PACKAGE_NAME", "com.example.ollie_app")

# The Flutter client owns the full set of product IDs (monthly,
# yearly, lifetime -- see purchase_service.dart) and passes whichever
# one was purchased straight through to /activate for verification.
# The backend only needs its own copy of the lifetime ID, since
# that's a one-time managed product verified via a different Play
# Developer API than the two auto-renewing subscriptions.
PLAY_MONTHLY_PRODUCT_ID = os.getenv("PLAY_MONTHLY_PRODUCT_ID", "ollie_premium_monthly")
PLAY_LIFETIME_PRODUCT_ID = os.getenv("PLAY_LIFETIME_PRODUCT_ID", "ollie_premium_lifetime")

# ============================================================
# CORS
# ============================================================

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
