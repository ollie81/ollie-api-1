# ============================================================
# Tests for event_scheduler.detect_explicit_reminder — the LLM
# extraction step (JSON parsing/validation), mocking the OpenAI
# call. The actual date arithmetic is covered separately in
# test_reminder_scheduling.py.
# ============================================================

import json
from unittest.mock import patch

from event_scheduler import detect_explicit_reminder, MAX_HOURS


def _llm_json(**overrides):
    base = {
        "is_reminder": True,
        "reminder_text": "call mom",
        "time_type": "relative",
        "relative_minutes": 20,
        "absolute_hour": None,
        "absolute_minute": None,
        "days_from_today": None,
    }
    base.update(overrides)
    return json.dumps(base)


def test_relative_reminder(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(_llm_json())):
        result = detect_explicit_reminder("remind me to call mom in 20 minutes", utc_offset_minutes=0)
    assert result == {"reminder_text": "call mom", "minutes_until": 20}


def test_absolute_reminder(mock_chat_completion):
    content = _llm_json(
        time_type="absolute", relative_minutes=None,
        absolute_hour=17, absolute_minute=0, days_from_today=0,
    )
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(content)):
        result = detect_explicit_reminder("remind me at 5pm", utc_offset_minutes=0)
    assert result is not None
    assert result["reminder_text"] == "call mom"
    assert 0 < result["minutes_until"] <= MAX_HOURS * 60


def test_not_a_reminder_returns_none(mock_chat_completion):
    content = json.dumps({
        "is_reminder": False, "reminder_text": "", "time_type": None,
        "relative_minutes": None, "absolute_hour": None,
        "absolute_minute": None, "days_from_today": None,
    })
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(content)):
        assert detect_explicit_reminder("how's the weather", utc_offset_minutes=0) is None


def test_empty_reminder_text_returns_none(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(_llm_json(reminder_text=""))):
        assert detect_explicit_reminder("remind me", utc_offset_minutes=0) is None


def test_unparseable_time_returns_none(mock_chat_completion):
    # time_type says "absolute" but the hour is missing -- the
    # request itself was recognized as a reminder, but there's
    # nothing sane to schedule against.
    content = _llm_json(time_type="absolute", relative_minutes=None, absolute_hour=None)
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(content)):
        assert detect_explicit_reminder("remind me sometime", utc_offset_minutes=0) is None


def test_malformed_json_returns_none_not_raises(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion("not valid json")):
        assert detect_explicit_reminder("remind me to call mom", utc_offset_minutes=0) is None


def test_empty_response_content_returns_none(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(None)):
        assert detect_explicit_reminder("remind me to call mom", utc_offset_minutes=0) is None


def test_api_exception_returns_none_not_raises():
    with patch("event_scheduler.openai_client.chat.completions.create", side_effect=Exception("timeout")):
        assert detect_explicit_reminder("remind me to call mom", utc_offset_minutes=0) is None


def test_empty_input_short_circuits_without_calling_api():
    with patch("event_scheduler.openai_client.chat.completions.create") as mock_create:
        assert detect_explicit_reminder("", utc_offset_minutes=0) is None
        mock_create.assert_not_called()


def test_wildly_out_of_range_day_gets_clamped(mock_chat_completion):
    # A hallucinated days_from_today must still land inside the
    # sanity bound, not produce an absurd multi-year schedule.
    content = _llm_json(
        time_type="absolute", relative_minutes=None,
        absolute_hour=12, absolute_minute=0, days_from_today=99999,
    )
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(content)):
        result = detect_explicit_reminder("remind me eventually", utc_offset_minutes=0)
    assert result["minutes_until"] == MAX_HOURS * 60
