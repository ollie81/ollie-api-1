# ============================================================
# Tests for chat._synthesize_speech — the shared TTS call used
# by both /speak and /speak/preview. Uses OpenAI TTS (the same
# OPENAI_API_KEY already configured for chat + Whisper), so
# there's no separate "is it configured" check the way the old
# Papla-based version needed -- openai_client is constructed at
# import time regardless of whether this function is ever called.
# Mocks chat.openai_client.audio.speech.create.
# ============================================================

from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

from chat import _synthesize_speech, OLLIE_TTS_VOICE


def test_success_returns_audio_bytes():
    with patch("chat.openai_client") as mock_openai:
        mock_openai.audio.speech.create.return_value = MagicMock(content=b"fake audio bytes")

        assert _synthesize_speech("hello") == b"fake audio bytes"


def test_uses_the_configured_ollie_voice():
    with patch("chat.openai_client") as mock_openai:
        mock_openai.audio.speech.create.return_value = MagicMock(content=b"audio")

        _synthesize_speech("hello there")

        call_kwargs = mock_openai.audio.speech.create.call_args.kwargs
        assert call_kwargs["voice"] == OLLIE_TTS_VOICE
        assert call_kwargs["input"] == "hello there"


def test_retries_then_succeeds():
    with patch("chat.openai_client") as mock_openai, patch("chat.time.sleep"):
        mock_openai.audio.speech.create.side_effect = [
            Exception("transient failure"),
            MagicMock(content=b"audio"),
        ]

        assert _synthesize_speech("hello") == b"audio"
        assert mock_openai.audio.speech.create.call_count == 2


def test_all_retries_fail_raises_500():
    with patch("chat.openai_client") as mock_openai, patch("chat.time.sleep"):
        mock_openai.audio.speech.create.side_effect = Exception("persistent failure")

        with pytest.raises(HTTPException) as exc_info:
            _synthesize_speech("hello")
        assert exc_info.value.status_code == 500
        assert mock_openai.audio.speech.create.call_count == 2
