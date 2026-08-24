# ============================================================
# Tests for event_scheduler.detect_explicit_reminder — the LLM
# extraction step (JSON parsing/validation), mocking the OpenAI
# call. The actual date arithmetic is covered separately in
# test_reminder_scheduling.py.
# ============================================================

import json
from unittest.mock import patch

from event_scheduler import detect_explicit_reminder, MAX_HOURS
from memory import FAST_MODEL, FLAGSHIP_MODEL


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


# ============================================================
# Fast-model miss on an obvious reminder retries once on the
# flagship model. This is the real-world bug: Ollie's chat reply
# (a separate model call) confidently promised a reminder for
# "don't forget to remind me to drink water after 10 minutes
# please", but this function's fast-model pass missed it -- no
# row was ever scheduled, so nothing fired, with no signal to the
# user that anything had gone wrong.
# ============================================================

def test_fast_model_miss_on_obvious_reminder_retries_and_succeeds(mock_chat_completion):
    not_a_reminder = json.dumps({
        "is_reminder": False, "reminder_text": "", "time_type": None,
        "relative_minutes": None, "absolute_hour": None,
        "absolute_minute": None, "days_from_today": None,
    })
    with patch("event_scheduler.openai_client.chat.completions.create") as mock_create:
        mock_create.side_effect = [
            mock_chat_completion(not_a_reminder),          # fast-model pass misses it
            mock_chat_completion(_llm_json(reminder_text="drink water", relative_minutes=10)),  # retry catches it
        ]
        result = detect_explicit_reminder(
            "now don't forget to remind me to drink water after 10 minutes please",
            utc_offset_minutes=0,
        )

    assert result == {"reminder_text": "drink water", "minutes_until": 10}
    assert mock_create.call_count == 2
    assert mock_create.call_args_list[0].kwargs["model"] == FAST_MODEL
    assert mock_create.call_args_list[1].kwargs["model"] == FLAGSHIP_MODEL


def test_fast_model_miss_without_a_reminder_cue_does_not_retry(mock_chat_completion):
    not_a_reminder = json.dumps({
        "is_reminder": False, "reminder_text": "", "time_type": None,
        "relative_minutes": None, "absolute_hour": None,
        "absolute_minute": None, "days_from_today": None,
    })
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(not_a_reminder)) as mock_create:
        result = detect_explicit_reminder("how's the weather today", utc_offset_minutes=0)

    assert result is None
    mock_create.assert_called_once()  # ordinary messages shouldn't pay for a second call


def test_both_passes_missing_still_returns_none_not_raises(mock_chat_completion):
    not_a_reminder = json.dumps({
        "is_reminder": False, "reminder_text": "", "time_type": None,
        "relative_minutes": None, "absolute_hour": None,
        "absolute_minute": None, "days_from_today": None,
    })
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(not_a_reminder)) as mock_create:
        result = detect_explicit_reminder("please remind me sometime maybe", utc_offset_minutes=0)

    assert result is None
    assert mock_create.call_count == 2
