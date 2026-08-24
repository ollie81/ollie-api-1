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
