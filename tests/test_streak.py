# ============================================================
# Tests for OllieDB.update_streak / get_streak — the daily streak
# counter. Mocks the supabase client, same pattern as
# test_is_premium_active.py.
#
# Streak days are the user's LOCAL calendar date (from
# utc_offset_minutes), not the server's UTC date -- several tests
# specifically check that an offset can push the local date across
# a UTC day boundary in either direction.
# ============================================================

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from database import OllieDB


def _mock_row(data):
    result = MagicMock()
    result.data = data
    return result


def _patch_user_row(mock_supabase, row):
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = \
        _mock_row(row)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def test_first_ever_message_starts_streak_at_one():
    with patch("database.supabase") as mock_supabase:
        _patch_user_row(mock_supabase, {"current_streak": 0, "last_streak_date": None})

        streak = OllieDB().update_streak("user-1", utc_offset_minutes=0)

        assert streak == 1
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["current_streak"] == 1
        assert update_call["last_streak_date"] == _today_utc().isoformat()


def test_already_credited_today_is_unchanged():
    with patch("database.supabase") as mock_supabase:
        _patch_user_row(mock_supabase, {
            "current_streak": 5,
            "last_streak_date": _today_utc().isoformat(),
        })

        streak = OllieDB().update_streak("user-1", utc_offset_minutes=0)

        assert streak == 5
        mock_supabase.table.return_value.update.assert_not_called()


def test_consecutive_local_day_increments():
    yesterday = _today_utc() - timedelta(days=1)
    with patch("database.supabase") as mock_supabase:
        _patch_user_row(mock_supabase, {"current_streak": 5, "last_streak_date": yesterday.isoformat()})

        streak = OllieDB().update_streak("user-1", utc_offset_minutes=0)

        assert streak == 6


def test_missed_day_resets_to_one():
    two_days_ago = _today_utc() - timedelta(days=2)
    with patch("database.supabase") as mock_supabase:
        _patch_user_row(mock_supabase, {"current_streak": 12, "last_streak_date": two_days_ago.isoformat()})

        streak = OllieDB().update_streak("user-1", utc_offset_minutes=0)

        assert streak == 1


def test_missed_many_days_resets_to_one():
    long_ago = _today_utc() - timedelta(days=30)
    with patch("database.supabase") as mock_supabase:
        _patch_user_row(mock_supabase, {"current_streak": 99, "last_streak_date": long_ago.isoformat()})

        streak = OllieDB().update_streak("user-1", utc_offset_minutes=0)

        assert streak == 1


def test_positive_offset_can_push_local_date_past_utc_midnight():
    # 23:50 UTC with a +60 min offset (e.g. much of Africa/Europe)
    # is 00:50 the next day locally -- the streak must credit
    # "tomorrow" (by UTC) as today, not lag a day behind.
    now_utc = datetime.now(timezone.utc).replace(hour=23, minute=50, second=0, microsecond=0)
    today_local = (now_utc + timedelta(minutes=60)).date()
    yesterday_local = today_local - timedelta(days=1)

    with patch("database.supabase") as mock_supabase, \
         patch("database.datetime") as mock_datetime:
        mock_datetime.now.return_value = now_utc
        mock_datetime.fromisoformat = datetime.fromisoformat
        _patch_user_row(mock_supabase, {"current_streak": 3, "last_streak_date": yesterday_local.isoformat()})

        streak = OllieDB().update_streak("user-1", utc_offset_minutes=60)

        assert streak == 4
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["last_streak_date"] == today_local.isoformat()


def test_no_offset_falls_back_to_utc():
    with patch("database.supabase") as mock_supabase:
        _patch_user_row(mock_supabase, {"current_streak": 2, "last_streak_date": None})

        streak = OllieDB().update_streak("user-1", utc_offset_minutes=None)

        assert streak == 1


def test_get_streak_returns_stored_value():
    with patch("database.supabase") as mock_supabase:
        _patch_user_row(mock_supabase, {"current_streak": 7})
        assert OllieDB().get_streak("user-1") == 7


def test_get_streak_defaults_to_zero_when_missing():
    with patch("database.supabase") as mock_supabase:
        _patch_user_row(mock_supabase, None)
        assert OllieDB().get_streak("user-1") == 0
