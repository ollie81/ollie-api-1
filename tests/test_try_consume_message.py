# ============================================================
# Tests for OllieDB.try_consume_message -- the atomic check-and-
# increment for the free-tier daily message cap. Replaces the old
# can_send_message + increment_message_count pair, which read the
# count and wrote it back as two separate steps: two concurrent
# requests could both read the same count and both pass, since
# neither would see the other's increment before deciding.
#
# Mocks the supabase client, same pattern as test_streak.py. The
# select chain (used by _get_usage_row) and the update chain are
# independent mocks, so a test can control what each "sees" on
# each retry attempt via side_effect lists.
# ============================================================

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from database import OllieDB


def _result(data):
    r = MagicMock()
    r.data = data
    return r


def _select_chain(mock_supabase):
    return mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute


def _update_chain(mock_supabase):
    return mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute


def _insert_chain(mock_supabase):
    return mock_supabase.table.return_value.insert.return_value.execute


def test_first_message_of_day_inserts_and_succeeds():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([])  # no row yet today

        assert OllieDB().try_consume_message("user-1") is True

        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert insert_call["count"] == 1


def test_under_limit_increments_and_succeeds():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"count": 5}])
        _update_chain(mock_supabase).return_value = _result([{"count": 6}])

        assert OllieDB().try_consume_message("user-1", limit=20) is True

        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["count"] == 6


def test_at_limit_returns_false_without_writing():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"count": 20}])

        assert OllieDB().try_consume_message("user-1", limit=20) is False
        mock_supabase.table.return_value.update.assert_not_called()
        mock_supabase.table.return_value.insert.assert_not_called()


def test_active_ad_bonus_bypasses_limit_without_writing():
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"count": 999, "ad_bonus_until": future}])

        assert OllieDB().try_consume_message("user-1", limit=20) is True
        mock_supabase.table.return_value.update.assert_not_called()


def test_expired_ad_bonus_does_not_bypass_limit():
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"count": 20, "ad_bonus_until": past}])

        assert OllieDB().try_consume_message("user-1", limit=20) is False


def test_concurrent_collision_retries_against_fresh_value_and_succeeds():
    # First attempt: reads count=5, but another request wins the
    # race first -- the conditional update matches zero rows (data
    # comes back empty). Second attempt: re-reads the now-current
    # count=6 (the winner's write) and its own conditional update
    # succeeds this time.
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).side_effect = [
            _result([{"count": 5}]),
            _result([{"count": 6}]),
        ]
        _update_chain(mock_supabase).side_effect = [
            _result([]),          # lost the race
            _result([{"count": 7}]),  # won on retry
        ]

        assert OllieDB().try_consume_message("user-1", limit=20) is True
        assert _update_chain(mock_supabase).call_count == 2


def test_exhausts_retries_and_fails_closed_under_perpetual_contention():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"count": 5}])
        _update_chain(mock_supabase).return_value = _result([])  # always loses

        assert OllieDB().try_consume_message("user-1", limit=20) is False
        assert _update_chain(mock_supabase).call_count == 5


def test_insert_race_on_first_message_retries_and_succeeds():
    # Two requests both see "no row yet" and both try to insert.
    # This one's insert loses (raises); on retry it should see the
    # winner's row and increment normally instead of inserting again.
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).side_effect = [
            _result([]),
            _result([{"count": 1}]),
        ]
        _insert_chain(mock_supabase).side_effect = Exception("duplicate key")
        _update_chain(mock_supabase).return_value = _result([{"count": 2}])

        assert OllieDB().try_consume_message("user-1") is True
        _insert_chain(mock_supabase).assert_called_once()
        _update_chain(mock_supabase).assert_called_once()
