# ============================================================
# Tests for the /speak route (chat.speak) -- specifically the
# defensive handling around the free voice trial. A DB hiccup
# checking or reading the trial balance must produce a clean,
# JSON-detailed error (or, for the read-only tail call, must never
# discard audio that was already paid for and synthesized) rather
# than an unhandled exception -- which Starlette's default handler
# turns into a plain-text response the Flutter client can't pull a
# message out of. Same bug class as the one hit on /chat/voice, see
# test_chat_voice.py.
#
# speak is a route function called directly (same style as
# test_settings_usage.py's get_usage calls), not through a
# TestClient. _synthesize_speech is mocked out -- its own behavior
# is covered by test_synthesize_speech.py.
# ============================================================

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from chat import speak, SpeakRequest


def _fake_request(path="/"):
    return Request(scope={
        "type": "http", "method": "POST", "path": path,
        "headers": [], "client": ("testclient", 123), "query_string": b"",
    })


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


def _run(message="hey there", user_id="user-1"):
    return speak(SpeakRequest(message=message), _fake_request(), current_user={"id": user_id})


def test_trial_check_exception_returns_clean_500_not_unhandled():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat._synthesize_speech") as mock_synth:
        _mock_db(mock_db_cls, trial_raises=Exception("connection reset"))

        with pytest.raises(HTTPException) as exc_info:
            _run()

        assert exc_info.value.status_code == 500
        # Never got far enough to pay for TTS on a request we
        # couldn't even confirm was allowed.
        mock_synth.assert_not_called()


def test_trial_balance_read_failure_still_returns_the_audio():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat._synthesize_speech", return_value=b"fake audio"):
        _mock_db(mock_db_cls, trial_ok=True, remaining_raises=Exception("connection reset"))

        response = _run()

        assert response.body == b"fake audio"
        assert "X-Voice-Trial-Remaining-Seconds" not in response.headers


def test_exhausted_trial_returns_402_before_synthesizing():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat._synthesize_speech") as mock_synth:
        _mock_db(mock_db_cls, trial_ok=False)

        with pytest.raises(HTTPException) as exc_info:
            _run()

        assert exc_info.value.status_code == 402
        mock_synth.assert_not_called()


def test_premium_user_never_touches_trial_at_all():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=True), \
         patch("chat._synthesize_speech", return_value=b"fake audio"):
        db = _mock_db(mock_db_cls)

        response = _run()

        db.try_consume_voice_trial.assert_not_called()
        db.get_voice_trial_remaining.assert_not_called()
        assert response.body == b"fake audio"
        assert "X-Voice-Trial-Remaining-Seconds" not in response.headers
