# ============================================================
# Tests for daily_message.py -- the proactive daily message
# scheduler. Mocks supabase and NotificationService, same pattern
# as test_streak.py / test_is_premium_active.py.
#
# _process_user is exercised directly (not the outer sweep) so
# each test controls exactly one user's row and "now" precisely.
# ============================================================

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import daily_message
from daily_message import _process_user, _window_bounds_utc


def _base_row(**overrides):
    row = {
        "id": "user-1",
        "last_known_utc_offset_minutes": 0,
        "last_daily_message_date": None,
        "next_daily_message_at": None,
    }
    row.update(overrides)
    return row


def test_already_sent_today_is_skipped():
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    today = now.date()
    row = _base_row(last_daily_message_date=today.isoformat())

    with patch("daily_message.supabase") as mock_supabase:
        _process_user(row, now)
        mock_supabase.table.assert_not_called()


def test_picks_a_target_within_window_on_first_tick():
    # Midday, well inside the window, offset 0 -- no target set yet.
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    row = _base_row()

    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message.NotificationService") as mock_notif:
        _process_user(row, now)

        # First tick only picks a target -- doesn't send yet.
        mock_notif.create_notification.assert_not_called()
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        target = datetime.fromisoformat(update_call["next_daily_message_at"])
        window_start, window_end = _window_bounds_utc(now.date(), timedelta(minutes=0))
        assert window_start <= target < window_end
        assert target >= now


def test_window_already_passed_today_sets_no_target():
    # After the window's end for today, offset 0, no target yet.
    now = datetime.now(timezone.utc).replace(hour=23, minute=0, second=0, microsecond=0)
    row = _base_row()

    with patch("daily_message.supabase") as mock_supabase:
        _process_user(row, now)
        mock_supabase.table.return_value.update.assert_not_called()


def test_sends_once_target_has_passed():
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    target = now - timedelta(minutes=5)
    row = _base_row(next_daily_message_at=target.isoformat())

    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message.NotificationService") as mock_notif, \
         patch("daily_message._generate_message", return_value="hey! thinking of you"):
        _process_user(row, now)

        mock_notif.create_notification.assert_called_once_with(
            user_id="user-1", title="Ollie", body="hey! thinking of you",
        )
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["last_daily_message_date"] == now.date().isoformat()
        assert update_call["next_daily_message_at"] is None


def test_target_not_yet_reached_does_nothing():
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    target = now + timedelta(minutes=30)
    row = _base_row(next_daily_message_at=target.isoformat())

    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message.NotificationService") as mock_notif:
        _process_user(row, now)
        mock_notif.create_notification.assert_not_called()
        mock_supabase.table.assert_not_called()


def test_stale_target_from_a_previous_day_is_recomputed():
    # A target left over from yesterday (e.g. the window closed
    # before it could fire) must not be treated as valid today.
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    stale_target = now - timedelta(days=1)
    row = _base_row(next_daily_message_at=stale_target.isoformat())

    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message.NotificationService") as mock_notif:
        _process_user(row, now)

        mock_notif.create_notification.assert_not_called()
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        new_target = datetime.fromisoformat(update_call["next_daily_message_at"])
        assert new_target > now


def test_positive_offset_uses_local_window_not_utc():
    # 06:00 UTC with a +180 min offset (e.g. East Africa) is 09:00
    # local -- inside the window even though it's early in UTC.
    now = datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0)
    row = _base_row(last_known_utc_offset_minutes=180)

    with patch("daily_message.supabase") as mock_supabase:
        _process_user(row, now)
        # A target should have been set (not skipped as outside the window).
        mock_supabase.table.return_value.update.assert_called_once()


def test_generation_failure_falls_back_to_a_template_line():
    with patch("daily_message.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.get_relevant_memories.side_effect = Exception("boom")
        result = daily_message._generate_message("user-1")
        assert result in daily_message.FALLBACK_LINES


def test_flagged_generation_falls_back_to_a_template_line():
    with patch("daily_message.OllieDB") as mock_db_cls, \
         patch("daily_message.build_memory_context", return_value="MEMORY: likes hiking"), \
         patch("daily_message.build_interest_context", return_value=""), \
         patch("daily_message.openai_client") as mock_client, \
         patch("daily_message.moderate_text", return_value={"categories": ["x"]}):
        mock_db_cls.return_value.get_relevant_memories.return_value = []
        mock_db_cls.return_value.get_user_context.return_value = {}
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="something flagged"))
        ]

        result = daily_message._generate_message("user-1")
        assert result in daily_message.FALLBACK_LINES
