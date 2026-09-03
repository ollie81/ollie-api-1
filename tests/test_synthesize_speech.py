# ============================================================
# Tests for chat._synthesize_speech — the shared TTS call used
# by both /speak and /speak/preview. Uses ElevenLabs (Ollie's
# cloned voice) when ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID are
# configured, else falls back to OpenAI TTS -- both paths retry
# once and raise HTTPException(500) on exhausted failure the same
# way, so each is tested for that behavior independently.
# ============================================================

from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

from chat import _synthesize_speech, OLLIE_TTS_VOICE


# ---- dispatch ----

def test_uses_elevenlabs_when_configured():
    with patch("chat.ELEVENLABS_API_KEY", "fake-key"), \
         patch("chat.ELEVENLABS_VOICE_ID", "fake-voice-id"), \
         patch("chat._synthesize_speech_elevenlabs", return_value=b"eleven audio") as mock_eleven, \
         patch("chat._synthesize_speech_openai") as mock_openai:

        assert _synthesize_speech("hello") == b"eleven audio"
        mock_eleven.assert_called_once_with("hello")
        mock_openai.assert_not_called()


def test_falls_back_to_openai_when_elevenlabs_unconfigured():
    with patch("chat.ELEVENLABS_API_KEY", None), \
         patch("chat.ELEVENLABS_VOICE_ID", None), \
         patch("chat._synthesize_speech_elevenlabs") as mock_eleven, \
         patch("chat._synthesize_speech_openai", return_value=b"openai audio") as mock_openai:

        assert _synthesize_speech("hello") == b"openai audio"
        mock_openai.assert_called_once_with("hello")
        mock_eleven.assert_not_called()


# ---- ElevenLabs path ----

def test_elevenlabs_success_returns_audio_bytes():
    with patch("chat.ELEVENLABS_API_KEY", "fake-key"), \
         patch("chat.ELEVENLABS_VOICE_ID", "fake-voice-id"), \
         patch("chat.requests.post") as mock_post:
        mock_post.return_value = MagicMock(content=b"fake audio bytes")

        from chat import _synthesize_speech_elevenlabs
        assert _synthesize_speech_elevenlabs("hello") == b"fake audio bytes"

        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["headers"]["xi-api-key"] == "fake-key"
        assert call_kwargs["json"]["text"] == "hello"
        assert "fake-voice-id" in mock_post.call_args.args[0]


def test_elevenlabs_retries_then_succeeds():
    with patch("chat.ELEVENLABS_API_KEY", "fake-key"), \
         patch("chat.ELEVENLABS_VOICE_ID", "fake-voice-id"), \
         patch("chat.requests.post") as mock_post, patch("chat.time.sleep"):
        mock_post.side_effect = [Exception("transient failure"), MagicMock(content=b"audio")]

        from chat import _synthesize_speech_elevenlabs
        assert _synthesize_speech_elevenlabs("hello") == b"audio"
        assert mock_post.call_count == 2


def test_elevenlabs_all_retries_fail_raises_500():
    with patch("chat.ELEVENLABS_API_KEY", "fake-key"), \
         patch("chat.ELEVENLABS_VOICE_ID", "fake-voice-id"), \
         patch("chat.requests.post") as mock_post, patch("chat.time.sleep"):
        mock_post.side_effect = Exception("persistent failure")

        from chat import _synthesize_speech_elevenlabs
        with pytest.raises(HTTPException) as exc_info:
            _synthesize_speech_elevenlabs("hello")
        assert exc_info.value.status_code == 500
        assert mock_post.call_count == 2


# ---- OpenAI fallback path ----

def test_openai_success_returns_audio_bytes():
    with patch("chat.openai_client") as mock_openai:
        mock_openai.audio.speech.create.return_value = MagicMock(content=b"fake audio bytes")

        from chat import _synthesize_speech_openai
        assert _synthesize_speech_openai("hello") == b"fake audio bytes"


def test_openai_uses_the_configured_ollie_voice():
    with patch("chat.openai_client") as mock_openai:
        mock_openai.audio.speech.create.return_value = MagicMock(content=b"audio")

        from chat import _synthesize_speech_openai
        _synthesize_speech_openai("hello there")

        call_kwargs = mock_openai.audio.speech.create.call_args.kwargs
        assert call_kwargs["voice"] == OLLIE_TTS_VOICE
        assert call_kwargs["input"] == "hello there"


def test_openai_retries_then_succeeds():
    with patch("chat.openai_client") as mock_openai, patch("chat.time.sleep"):
        mock_openai.audio.speech.create.side_effect = [
            Exception("transient failure"),
            MagicMock(content=b"audio"),
        ]

        from chat import _synthesize_speech_openai
        assert _synthesize_speech_openai("hello") == b"audio"
        assert mock_openai.audio.speech.create.call_count == 2


def test_openai_all_retries_fail_raises_500():
    with patch("chat.openai_client") as mock_openai, patch("chat.time.sleep"):
        mock_openai.audio.speech.create.side_effect = Exception("persistent failure")

        from chat import _synthesize_speech_openai
        with pytest.raises(HTTPException) as exc_info:
            _synthesize_speech_openai("hello")
        assert exc_info.value.status_code == 500
        assert mock_openai.audio.speech.create.call_count == 2
