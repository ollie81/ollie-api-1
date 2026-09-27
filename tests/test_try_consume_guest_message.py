# ============================================================
# Tests for OllieDB.try_consume_guest_message -- the atomic
# check-and-increment for a guest session's one-time lifetime cap
# (see auth.py's /auth/guest). Same optimistic-concurrency shape as
# try_consume_message (test_try_consume_message.py), simpler
# underlying data: a single counter column on the user's own row,
# no date-partitioned table, no ad-bonus bypass.
# ============================================================

from unittest.mock import patch, MagicMock

from database import OllieDB


def _result(data):
    r = MagicMock()
    r.data = data
    return r


def _select_chain(mock_supabase):
    return mock_supabase.table.return_value.select.return_value.eq.return_value.execute


def _update_chain(mock_supabase):
    return mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute


def test_unknown_user_returns_false():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([])

        assert OllieDB().try_consume_guest_message("guest-1") is False
        mock_supabase.table.return_value.update.assert_not_called()


def test_under_limit_increments_and_succeeds():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"guest_messages_used": 3}])
        _update_chain(mock_supabase).return_value = _result([{"guest_messages_used": 4}])

        assert OllieDB().try_consume_guest_message("guest-1") is True

        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["guest_messages_used"] == 4


def test_missing_counter_defaults_to_zero_and_succeeds():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{}])  # column present but null
        _update_chain(mock_supabase).return_value = _result([{"guest_messages_used": 1}])

        assert OllieDB().try_consume_guest_message("guest-1") is True

        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["guest_messages_used"] == 1


def test_at_limit_returns_false_without_writing():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"guest_messages_used": OllieDB.GUEST_MESSAGE_LIMIT}])

        assert OllieDB().try_consume_guest_message("guest-1") is False
        mock_supabase.table.return_value.update.assert_not_called()


def test_concurrent_collision_retries_against_fresh_value_and_succeeds():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).side_effect = [
            _result([{"guest_messages_used": 3}]),
            _result([{"guest_messages_used": 4}]),
        ]
        _update_chain(mock_supabase).side_effect = [
            _result([]),  # lost the race
            _result([{"guest_messages_used": 5}]),  # won on retry
        ]

        assert OllieDB().try_consume_guest_message("guest-1") is True
        assert _update_chain(mock_supabase).call_count == 2


def test_exhausts_retries_and_fails_closed_under_perpetual_contention():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"guest_messages_used": 3}])
        _update_chain(mock_supabase).return_value = _result([])  # always loses

        assert OllieDB().try_consume_guest_message("guest-1") is False
        assert _update_chain(mock_supabase).call_count == 5
