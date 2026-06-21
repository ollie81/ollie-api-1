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
# CORS
# ============================================================

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
