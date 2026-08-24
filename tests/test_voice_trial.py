# ============================================================
# Tests for OllieDB.try_consume_voice_trial -- the free one-time
# ~60-second voice trial for non-premium users, gating the real
# /speak endpoint (not the separate canned /speak/preview line).
# Same optimistic-concurrency shape as try_consume_message, see
# test_try_consume_message.py.
# ============================================================

from unittest.mock import patch, MagicMock

from database import OllieDB, TRIAL_VOICE_SECONDS_LIMIT, ESTIMATED_CHARS_PER_SECOND


def _result(data):
    r = MagicMock()
    r.data = data
    return r


def _select_chain(mock_supabase):
    return mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute


def _update_chain(mock_supabase):
    return mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute


def test_fresh_user_can_consume_a_short_message():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result({"voice_trial_seconds_used": 0})
        _update_chain(mock_supabase).return_value = _result([{"voice_trial_seconds_used": 5}])

        assert OllieDB().try_consume_voice_trial("user-1", "hey there!") is True


def test_estimated_duration_is_deducted_correctly():
    text = "a" * (ESTIMATED_CHARS_PER_SECOND * 10)  # ~10 estimated seconds
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result({"voice_trial_seconds_used": 0})
        _update_chain(mock_supabase).return_value = _result([{}])

        assert OllieDB().try_consume_voice_trial("user-1", text) is True

        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["voice_trial_seconds_used"] == 10


def test_exhausted_budget_is_rejected_without_writing():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result({"voice_trial_seconds_used": TRIAL_VOICE_SECONDS_LIMIT})

        assert OllieDB().try_consume_voice_trial("user-1", "hey") is False
        mock_supabase.table.return_value.update.assert_not_called()


def test_message_that_would_exceed_remaining_budget_is_rejected():
    # 55 used, 5 remaining -- a message estimated well over 5s must
    # not be allowed to push the total past the limit.
    long_text = "a" * (ESTIMATED_CHARS_PER_SECOND * 20)  # ~20 estimated seconds
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result({"voice_trial_seconds_used": 55})

        assert OllieDB().try_consume_voice_trial("user-1", long_text) is False
        mock_supabase.table.return_value.update.assert_not_called()


def test_never_used_field_defaults_to_zero():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result({})  # no voice_trial_seconds_used key at all
        _update_chain(mock_supabase).return_value = _result([{}])

        assert OllieDB().try_consume_voice_trial("user-1", "hey") is True


def test_concurrent_collision_retries_against_fresh_value_and_succeeds():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).side_effect = [
            _result({"voice_trial_seconds_used": 10}),
            _result({"voice_trial_seconds_used": 12}),
        ]
        _update_chain(mock_supabase).side_effect = [
            _result([]),      # lost the race
            _result([{}]),    # won on retry
        ]

        assert OllieDB().try_consume_voice_trial("user-1", "hey there") is True
        assert _update_chain(mock_supabase).call_count == 2


def test_exhausts_retries_and_fails_closed_under_perpetual_contention():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result({"voice_trial_seconds_used": 0})
        _update_chain(mock_supabase).return_value = _result([])  # always loses

        assert OllieDB().try_consume_voice_trial("user-1", "hey") is False
        assert _update_chain(mock_supabase).call_count == 5
