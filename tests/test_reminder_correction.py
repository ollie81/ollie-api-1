# ============================================================
# Tests for event_scheduler._try_correct_recent_reminder and its
# wiring into maybe_schedule_reminder.
#
# Real bug report: a typo in a reminder request ("remind me to go
# to it in 10 minutes") followed immediately by a correction
# ("I mean to eat not to it") left the reminder scheduled with the
# typo'd text forever -- each message is checked independently for
# whether IT is a new reminder request, and a correction on its own
# doesn't look like one, so it was silently ignored while the
# original (wrong) reminder stayed scheduled as-is.
# ============================================================

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from event_scheduler import (
    _looks_like_reminder_correction,
    _try_correct_recent_reminder,
    maybe_schedule_reminder,
)


def _result(data):
    r = MagicMock()
    r.data = data
    return r


def _recent_reminder_query_chain(mock_supabase):
    # Three chained .eq() calls in the real query -- each one calls
    # .eq() on the PREVIOUS call's return value, not the original
    # object, so the mock path nests three .eq.return_value deep
    # rather than collapsing to one.
    return (
        mock_supabase.table.return_value.select.return_value
        .eq.return_value.eq.return_value.eq.return_value
        .gte.return_value.order.return_value.limit.return_value.execute
    )


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def test_looks_like_correction_matches_common_phrasing():
    assert _looks_like_reminder_correction("I mean to eat not to it") is True
    assert _looks_like_reminder_correction("i meant water not walk") is True
    assert _looks_like_reminder_correction("sorry, meant to say drink water") is True


def test_looks_like_correction_does_not_match_unrelated_text():
    assert _looks_like_reminder_correction("how's the weather today") is False
    assert _looks_like_reminder_correction("") is False


def test_non_correction_message_never_touches_the_db():
    with patch("event_scheduler.supabase") as mock_supabase:
        assert _try_correct_recent_reminder("user-1", "how's the weather today", NOW) is False
        mock_supabase.table.assert_not_called()


def test_no_recent_pending_reminder_does_nothing():
    with patch("event_scheduler.supabase") as mock_supabase:
        _recent_reminder_query_chain(mock_supabase).return_value = _result([])

        assert _try_correct_recent_reminder("user-1", "i mean eat not it", NOW) is False
        mock_supabase.table.return_value.update.assert_not_called()


def test_real_correction_updates_the_recent_reminder(mock_chat_completion):
    with patch("event_scheduler.supabase") as mock_supabase, \
         patch("event_scheduler.openai_client.chat.completions.create") as mock_create, \
         patch("event_scheduler.moderate_text", return_value=None):
        _recent_reminder_query_chain(mock_supabase).return_value = _result(
            [{"id": "evt-1", "event_summary": "go to it"}]
        )
        mock_create.side_effect = [
            mock_chat_completion("eat"),                        # correction extraction
            mock_chat_completion("hey — don't forget to eat!"), # personalization
        ]

        assert _try_correct_recent_reminder("user-1", "i mean eat not it", NOW) is True

        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["event_summary"] == "eat"
        assert update_call["notification_body"] == "hey — don't forget to eat!"
        mock_supabase.table.return_value.update.return_value.eq.assert_called_with("id", "evt-1")


def test_llm_says_not_a_correction_does_not_update(mock_chat_completion):
    with patch("event_scheduler.supabase") as mock_supabase, \
         patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion("")):
        _recent_reminder_query_chain(mock_supabase).return_value = _result(
            [{"id": "evt-1", "event_summary": "go to it"}]
        )

        assert _try_correct_recent_reminder("user-1", "i mean something unrelated", NOW) is False
        mock_supabase.table.return_value.update.assert_not_called()


def test_extraction_exception_fails_safe():
    with patch("event_scheduler.supabase") as mock_supabase, \
         patch("event_scheduler.openai_client.chat.completions.create",
               side_effect=Exception("timeout")):
        _recent_reminder_query_chain(mock_supabase).return_value = _result(
            [{"id": "evt-1", "event_summary": "go to it"}]
        )

        assert _try_correct_recent_reminder("user-1", "i mean eat not it", NOW) is False
        mock_supabase.table.return_value.update.assert_not_called()


def test_maybe_schedule_reminder_tries_correction_when_not_a_new_reminder(mock_chat_completion):
    not_a_reminder_json = (
        '{"is_reminder": false, "reminder_text": "", "time_type": null, '
        '"relative_minutes": null, "absolute_hour": null, "absolute_minute": null, '
        '"days_from_today": null}'
    )
    with patch("event_scheduler.openai_client.chat.completions.create") as mock_create, \
         patch("event_scheduler.moderate_text", return_value=None), \
         patch("event_scheduler.supabase") as mock_supabase:
        mock_create.side_effect = [
            mock_chat_completion(not_a_reminder_json),           # detect_explicit_reminder (fast model)
            mock_chat_completion("eat"),                         # correction extraction
            mock_chat_completion("hey don't forget to eat!"),    # personalization
        ]
        _recent_reminder_query_chain(mock_supabase).return_value = _result(
            [{"id": "evt-1", "event_summary": "go to it"}]
        )

        maybe_schedule_reminder("user-1", "i mean eat not it", utc_offset_minutes=0)

        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["event_summary"] == "eat"
