# ============================================================
# CONFTEST — fake credentials so importing the app's modules
# doesn't fail or attempt a real network call at import time.
# ============================================================
# config.py raises if JWT_SECRET is missing, and several modules
# construct real SDK clients (Supabase, OpenAI, Twilio) at module
# import time. Client construction is lazy for all three SDKs used
# here — it doesn't make a network call or validate the credential
# — so a fake-but-well-formed value is enough to import cleanly.
# None of these are ever used to make a real request in this suite.
#
# Must run before any app module is imported, so this lives at
# the top of conftest.py (pytest loads it before collecting tests)
# rather than inside a fixture.

import os

os.environ.setdefault("JWT_SECRET", "test-secret-not-real")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key-not-real")
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "test-sid-not-real")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-token-not-real")
os.environ.setdefault("TWILIO_VERIFY_SERVICE_SID", "test-verify-sid-not-real")
os.environ.setdefault("TWILIO_PHONE_NUMBER", "+10000000000")
