# ============================================================
# Tests for the /chat/voice route (chat.chat_voice) -- the free
# voice trial added alongside the existing /speak trial. Same
# shared TRIAL_VOICE_SECONDS_LIMIT budget, but charged a flat
# VOICE_INPUT_TRIAL_COST_SECONDS per turn instead of a text-based
# estimate, since the transcribed text (and therefore its length)
# isn't known until AFTER the one call worth gating against
# (Whisper) has already run.
#
# chat_voice is a route function called directly (same style as
# test_settings_usage.py's get_usage calls) rather than through a
# TestClient -- it's async, so calls go through asyncio.run.
# _process_chat_message is mocked out entirely: its own behavior
# is out of scope here, only the trial-gating wrapper around it.
# ============================================================

import asyncio
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

from chat import chat_voice
from database import VOICE_INPUT_TRIAL_COST_SECONDS


class _FakeUpload:
    def __init__(self, data: bytes, filename: str = "voice.m4a"):
        self._data = data
        self.filename = filename

    async def read(self):
        return self._data


def _run(audio_bytes=b"fake audio bytes", utc_offset_minutes=None, user_id="user-1"):
    return asyncio.run(chat_voice(
        audio=_FakeUpload(audio_bytes),
        utc_offset_minutes=utc_offset_minutes,
        current_user={"id": user_id},
    ))


def _mock_db(mock_db_cls, *, trial_ok=True, remaining=42, trial_raises=None, remaining_raises=None):
    instance = mock_db_cls.return_value
    if trial_raises:
        instance.try_consume_voice_trial.side_effect = trial_raises
    else:
        instance.try_consume_voice_trial.return_value = trial_ok
    if remaining_raises:
        instance.get_voice_trial_remaining.side_effect = remaining_raises
    else:
        instance.get_voice_trial_remaining.return_value = remaining
    return instance


def _mock_transcription(mock_openai, text="hello there"):
    mock_openai.audio.transcriptions.create.return_value = MagicMock(text=text)


def test_premium_user_skips_trial_entirely():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=True), \
         patch("chat.openai_client") as mock_openai, \
         patch("chat._process_chat_message", return_value={"reply": "hey!"}) as mock_process:
        db = _mock_db(mock_db_cls)
        _mock_transcription(mock_openai)

        result = _run()

        db.try_consume_voice_trial.assert_not_called()
        mock_process.assert_called_once()
        assert result["transcribed_text"] == "hello there"
        assert "voice_trial_seconds_remaining" not in result


def test_non_premium_user_with_budget_is_charged_flat_cost():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai, \
         patch("chat._process_chat_message", return_value={"reply": "hey!"}):
        db = _mock_db(mock_db_cls, trial_ok=True, remaining=50)
        _mock_transcription(mock_openai)

        result = _run()

        db.try_consume_voice_trial.assert_called_once_with("user-1", VOICE_INPUT_TRIAL_COST_SECONDS)
        assert result["voice_trial_seconds_remaining"] == 50


def test_non_premium_user_without_budget_gets_402_before_transcribing():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai:
        _mock_db(mock_db_cls, trial_ok=False)

        with pytest.raises(HTTPException) as exc_info:
            _run()

        assert exc_info.value.status_code == 402
        # The whole point of gating first: never pay for Whisper on
        # a turn that was never going to be allowed through.
        mock_openai.audio.transcriptions.create.assert_not_called()


def test_empty_audio_rejected_before_charging_trial():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai:
        db = _mock_db(mock_db_cls, trial_ok=True)

        with pytest.raises(HTTPException) as exc_info:
            _run(audio_bytes=b"")

        assert exc_info.value.status_code == 400
        db.try_consume_voice_trial.assert_not_called()
        mock_openai.audio.transcriptions.create.assert_not_called()


def test_silent_recording_still_returns_400_after_charging_trial():
    # Documents an accepted trade-off, not a bug: the trial is
    # charged up front (before Whisper) so a non-premium user can
    # be rejected without ever paying for transcription. That means
    # a silent/unintelligible recording still costs trial seconds
    # even though nothing useful came back -- same shape /speak
    # already accepts when _synthesize_speech fails after a
    # successful try_consume_voice_trial call.
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai:
        db = _mock_db(mock_db_cls, trial_ok=True)
        _mock_transcription(mock_openai, text="   ")

        with pytest.raises(HTTPException) as exc_info:
            _run()

        assert exc_info.value.status_code == 400
        db.try_consume_voice_trial.assert_called_once()
        mock_openai.audio.transcriptions.create.assert_called_once()


def test_trial_check_exception_returns_clean_500_not_unhandled():
    # Regression: a raw (non-HTTPException) exception here used to
    # propagate unhandled, which Starlette's default error handler
    # turns into a plain-text 500 the Flutter client can't pull a
    # message out of -- it fell back to a generic "Failed to process
    # voice message" with no clue what actually happened.
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai:
        _mock_db(mock_db_cls, trial_raises=Exception("connection reset"))

        with pytest.raises(HTTPException) as exc_info:
            _run()

        assert exc_info.value.status_code == 500
        # Never got far enough to pay for Whisper on a request we
        # couldn't even confirm was allowed.
        mock_openai.audio.transcriptions.create.assert_not_called()


def test_trial_balance_read_failure_does_not_discard_successful_reply():
    # Regression: by this point the reply is already fully processed
    # and saved (conversation history, memory, streak...). A hiccup
    # reading the trial balance afterward must not turn an already-
    # successful turn into a hard failure for the client.
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai, \
         patch("chat._process_chat_message", return_value={"reply": "hey!"}):
        _mock_db(mock_db_cls, trial_ok=True, remaining_raises=Exception("connection reset"))
        _mock_transcription(mock_openai)

        result = _run()

        assert result["reply"] == "hey!"
        assert result["transcribed_text"] == "hello there"
        assert "voice_trial_seconds_remaining" not in result


def test_premium_status_reused_for_response_shape_not_rechecked():
    # is_premium_active's result is what decides whether
    # voice_trial_seconds_remaining is even computed -- a premium
    # user should never trigger the extra get_voice_trial_remaining
    # read.
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=True), \
         patch("chat.openai_client") as mock_openai, \
         patch("chat._process_chat_message", return_value={"reply": "hey!"}):
        db = _mock_db(mock_db_cls)
        _mock_transcription(mock_openai)

        _run()

        db.get_voice_trial_remaining.assert_not_called()
