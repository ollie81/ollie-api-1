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
# Grace window for a just-rotated refresh token instead of deleting
# it immediately -- see /auth/refresh in auth.py.
REFRESH_TOKEN_GRACE_SECONDS = 60

# ============================================================
# GOOGLE PLAY BILLING (real purchase verification)
# ============================================================

GOOGLE_PLAY_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON")
ANDROID_PACKAGE_NAME = os.getenv("ANDROID_PACKAGE_NAME", "com.oliviranzi.ollie")
# Keep rewarded-ad credits disabled until a trusted server-side verification
# callback is configured. A client-controlled flag is not sufficient.
AD_REWARD_VERIFICATION_ENABLED = os.getenv("AD_REWARD_VERIFICATION_ENABLED", "false").lower() == "true"

# The Flutter client owns the full set of product IDs (monthly,
# yearly, lifetime -- see purchase_service.dart) and passes whichever
# one was purchased straight through to /activate for verification.
# The backend only needs its own copy of the lifetime ID, since
# that's a one-time managed product verified via a different Play
# Developer API than the two auto-renewing subscriptions.
PLAY_MONTHLY_PRODUCT_ID = os.getenv("PLAY_MONTHLY_PRODUCT_ID", "ollie_premium_monthly")
PLAY_LIFETIME_PRODUCT_ID = os.getenv("PLAY_LIFETIME_PRODUCT_ID", "ollie_premium_lifetime")

# ============================================================
# LEMON SQUEEZY (web premium purchases) — the web client has no
# app-store equivalent to Google Play Billing, so it buys premium
# through Lemon Squeezy instead (see billing.py). Stripe doesn't
# support Rwanda as a merchant country, and Flutterwave requires
# business registration that isn't available -- Lemon Squeezy is a
# Merchant of Record (it's the legal seller, not us), which is what
# lets an individual sell here without registering a business, and
# it confirmed Rwanda for payouts. Optional, same no-op-if-unset
# pattern as the other third-party keys here: absent just means
# /billing/create-checkout-session 500s until set.
# ============================================================

LEMONSQUEEZY_API_KEY = os.getenv("LEMONSQUEEZY_API_KEY")
LEMONSQUEEZY_STORE_ID = os.getenv("LEMONSQUEEZY_STORE_ID")
# Variant ids for the monthly/yearly subscription products -- create
# these in the Lemon Squeezy dashboard first (Products > New Product,
# with two variants) -- see billing.py for the full setup steps.
LEMONSQUEEZY_VARIANT_MONTHLY = os.getenv("LEMONSQUEEZY_VARIANT_MONTHLY")
LEMONSQUEEZY_VARIANT_YEARLY = os.getenv("LEMONSQUEEZY_VARIANT_YEARLY")
LEMONSQUEEZY_WEBHOOK_SECRET = os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET")

# ============================================================
# WEB APP
# ============================================================

# Where Lemon Squeezy's hosted checkout sends the browser back to
# after payment, and whatever else ends up keying off the web app's
# own origin (CORS, etc).
WEB_APP_URL = os.getenv("WEB_APP_URL", "http://localhost:5173")

# ============================================================
# CORS
# ============================================================

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

# ============================================================
# ERROR MONITORING (Sentry) — optional. Absent/empty means the SDK
# initializes as a clean no-op (Sentry's own documented behavior for
# dsn=None), same "third-party service you haven't set up yet doesn't
# break anything" pattern as GOOGLE_PLAY_SERVICE_ACCOUNT_JSON above.
# ============================================================

SENTRY_DSN = os.getenv("SENTRY_DSN")

# Set false for web-worker deployments where a separate scheduler/worker
# process is responsible for periodic jobs.
SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"

# ============================================================
# ELEVENLABS (Ollie's cloned voice) — optional. Absent means
# chat._synthesize_speech falls back to OpenAI's preset TTS voice,
# same no-op-if-unset pattern as everything else on this page.
# ============================================================

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
