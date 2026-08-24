# ============================================================
# Tests for OllieDB.grant_ad_bonus -- same atomic optimistic-
# concurrency shape as try_consume_message / try_consume_voice_trial
# (see test_try_consume_message.py). Was previously a plain read-
# then-write: two concurrent grants (a retried request, a second
# device) could both read the same ads_watched and both get
# through, undermining MAX_AD_WATCHES_PER_DAY.
# ============================================================

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from database import OllieDB, MAX_AD_WATCHES_PER_DAY


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


def test_first_grant_of_day_inserts_and_succeeds():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([])  # no row yet today

        assert OllieDB().grant_ad_bonus("user-1") is True

        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert insert_call["ads_watched"] == 1


def test_under_cap_increments_and_succeeds():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"ads_watched": 1}])
        _update_chain(mock_supabase).return_value = _result([{"ads_watched": 2}])

        assert OllieDB().grant_ad_bonus("user-1") is True

        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["ads_watched"] == 2


def test_at_cap_returns_false_without_writing():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"ads_watched": MAX_AD_WATCHES_PER_DAY}])

        assert OllieDB().grant_ad_bonus("user-1") is False
        mock_supabase.table.return_value.update.assert_not_called()
        mock_supabase.table.return_value.insert.assert_not_called()


def test_concurrent_collision_retries_against_fresh_value_and_succeeds():
    # First attempt reads ads_watched=1, but another request (e.g. a
    # retried call after a dropped response) wins the race first --
    # the conditional update matches zero rows. Second attempt
    # re-reads the now-current ads_watched=2 and succeeds.
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).side_effect = [
            _result([{"ads_watched": 1}]),
            _result([{"ads_watched": 2}]),
        ]
        _update_chain(mock_supabase).side_effect = [
            _result([]),               # lost the race
            _result([{"ads_watched": 3}]),  # won on retry
        ]

        assert OllieDB().grant_ad_bonus("user-1") is True
        assert _update_chain(mock_supabase).call_count == 2


def test_exhausts_retries_and_fails_closed_under_perpetual_contention():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([{"ads_watched": 1}])
        _update_chain(mock_supabase).return_value = _result([])  # always loses

        assert OllieDB().grant_ad_bonus("user-1") is False
        assert _update_chain(mock_supabase).call_count == 5


def test_insert_race_on_first_grant_retries_and_succeeds():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).side_effect = [
            _result([]),
            _result([{"ads_watched": 1}]),
        ]
        _insert_chain(mock_supabase).side_effect = Exception("duplicate key")
        _update_chain(mock_supabase).return_value = _result([{"ads_watched": 2}])

        assert OllieDB().grant_ad_bonus("user-1") is True
        _insert_chain(mock_supabase).assert_called_once()
        _update_chain(mock_supabase).assert_called_once()


def test_bonus_until_is_set_minutes_in_the_future():
    with patch("database.supabase") as mock_supabase:
        _select_chain(mock_supabase).return_value = _result([])

        before = datetime.now(timezone.utc)
        OllieDB().grant_ad_bonus("user-1", minutes=10)

        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        bonus_until = datetime.fromisoformat(insert_call["ad_bonus_until"])
        assert bonus_until > before + timedelta(minutes=9)
        assert bonus_until < before + timedelta(minutes=11)
