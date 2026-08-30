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

import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """
    Every @limiter.limit(...)-decorated route is exercised here by
    calling the route function directly with a minimal synthetic
    Request (real starlette.requests.Request -- slowapi rejects a
    MagicMock). That synthetic request always has the same fake
    client address and a generic path, so without this, slowapi's
    in-memory counters -- which persist for the life of the process,
    not just one test -- silently pool call counts across unrelated
    tests and even unrelated routes, eventually tripping
    RateLimitExceeded on a test that did nothing wrong. Reset all
    three Limiter instances before every test so each one starts
    from a clean slate.
    """
    import auth
    import chat
    import app as app_module
    auth.limiter.reset()
    chat.limiter.reset()
    app_module.limiter.reset()
    yield


@pytest.fixture
def mock_chat_completion():
    """
    Factory fixture: call it with a JSON string (or None, for the
    "empty response" case) to get an object shaped like what
    `openai_client.chat.completions.create(...)` returns, so code
    that reads `response.choices[0].message.content` works against
    it without a real network call.
    """
    def _make(content):
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = content
        return response
    return _make


@pytest.fixture
def mock_moderation_response():
    """
    Same idea for `openai_client.moderations.create(...)` — reads
    `response.results[0].flagged` and
    `response.results[0].categories.model_dump()`.
    """
    def _make(flagged: bool, categories: dict):
        response = MagicMock()
        response.results = [MagicMock()]
        response.results[0].flagged = flagged
        response.results[0].categories.model_dump.return_value = categories
        return response
    return _make
