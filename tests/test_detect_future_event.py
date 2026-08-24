# ============================================================
# Tests for event_scheduler.detect_future_event — mocking the
# OpenAI call.
# ============================================================

import json
from unittest.mock import patch

from event_scheduler import detect_future_event, MIN_HOURS, MAX_HOURS


def _llm_json(**overrides):
    base = {"has_future_event": True, "event_summary": "job interview", "hours_until_checkin": 24}
    base.update(overrides)
    return json.dumps(base)


def test_future_event_detected(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(_llm_json())):
        result = detect_future_event("I have a job interview tomorrow")
    assert result == {"event_summary": "job interview", "hours_until_checkin": 24}


def test_no_future_event_returns_none(mock_chat_completion):
    content = json.dumps({"has_future_event": False, "event_summary": "", "hours_until_checkin": None})
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(content)):
        assert detect_future_event("what's up") is None


def test_empty_summary_returns_none(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(_llm_json(event_summary=""))):
        assert detect_future_event("something happens sometime") is None


def test_hours_below_minimum_clamped_up(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(_llm_json(hours_until_checkin=0))):
        assert detect_future_event("something")["hours_until_checkin"] == MIN_HOURS


def test_hours_above_maximum_clamped_down(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(_llm_json(hours_until_checkin=999999))):
        assert detect_future_event("something")["hours_until_checkin"] == MAX_HOURS


def test_malformed_json_returns_none_not_raises(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion("nonsense")):
        assert detect_future_event("i have an exam") is None


def test_empty_response_content_returns_none(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(None)):
        assert detect_future_event("i have an exam") is None


def test_api_exception_returns_none_not_raises():
    with patch("event_scheduler.openai_client.chat.completions.create", side_effect=Exception("timeout")):
        assert detect_future_event("i have an exam") is None


def test_empty_input_short_circuits_without_calling_api():
    with patch("event_scheduler.openai_client.chat.completions.create") as mock_create:
        assert detect_future_event("") is None
        mock_create.assert_not_called()
