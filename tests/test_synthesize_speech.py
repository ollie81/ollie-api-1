# ============================================================
# Tests for chat._synthesize_speech — the shared TTS call used
# by both /speak and /speak/preview. Mocks requests.post.
#
# PAPLA_API_KEY/OLLIE_VOICE_ID are bound at chat.py's import time
# from config (which reads them as unset in the test env), so
# every test that needs to get past the "not configured" check
# patches those two module-level names directly.
# ============================================================

from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

from chat import _synthesize_speech


def _configured():
    return patch.multiple("chat", PAPLA_API_KEY="test-key", OLLIE_VOICE_ID="test-voice-id")


def test_not_configured_raises_500():
    # No patching here -- PAPLA_API_KEY/OLLIE_VOICE_ID are genuinely
    # unset in the test environment.
    with pytest.raises(HTTPException) as exc_info:
        _synthesize_speech("hello")
    assert exc_info.value.status_code == 500


def test_success_returns_audio_bytes():
    with _configured(), patch("chat.requests.post") as mock_post:
        response = MagicMock()
        response.status_code = 200
        response.content = b"fake audio bytes"
        mock_post.return_value = response

        assert _synthesize_speech("hello") == b"fake audio bytes"


def test_retries_then_succeeds():
    with _configured(), patch("chat.time.sleep"), patch("chat.requests.post") as mock_post:
        fail = MagicMock(status_code=500)
        success = MagicMock(status_code=200, content=b"audio")
        mock_post.side_effect = [fail, success]

        assert _synthesize_speech("hello") == b"audio"
        assert mock_post.call_count == 2


def test_all_retries_fail_raises_500():
    with _configured(), patch("chat.time.sleep"), patch("chat.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=500)

        with pytest.raises(HTTPException) as exc_info:
            _synthesize_speech("hello")
        assert exc_info.value.status_code == 500


def test_network_exception_raises_500_not_propagated():
    import requests
    with _configured(), patch("chat.time.sleep"), \
         patch("chat.requests.post", side_effect=requests.RequestException("timeout")):
        with pytest.raises(HTTPException) as exc_info:
            _synthesize_speech("hello")
        assert exc_info.value.status_code == 500
