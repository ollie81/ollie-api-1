# ============================================================
# Tests for daily_message.py -- the two proactive daily moments
# (morning check-in, nightly recap). Mocks supabase and
# NotificationService, same pattern as test_streak.py.
#
# _due_check is exercised directly for the shared scheduling shape
# (each test controls exactly one user's row and "now" precisely),
# then _process_morning_checkin / _process_nightly_recap for the
# thin per-job wiring, then run_daily_messages for the sweep.
# ============================================================

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import daily_message
from daily_message import (
    _due_check,
    _process_morning_checkin,
    _process_nightly_recap,
    run_daily_messages,
    MORNING_WINDOW_START_HOUR,
    MORNING_WINDOW_END_HOUR,
)


def _base_row(**overrides):
    row = {
        "id": "user-1",
        "last_known_utc_offset_minutes": 0,
        "last_daily_message_date": None,
        "next_daily_message_at": None,
        "last_nightly_recap_date": None,
        "next_nightly_recap_at": None,
    }
    row.update(overrides)
    return row


# ---- _due_check (shared scheduling shape) ----

def test_already_sent_today_is_skipped():
    now = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    today = now.date()
    row = _base_row(last_daily_message_date=today.isoformat())

    due, update, _ = _due_check(row, now, 7, 11, "last_daily_message_date", "next_daily_message_at")
    assert due is False
    assert update is None


def test_picks_a_target_within_window_on_first_tick():
    now = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)
    row = _base_row()

    due, update, today_local = _due_check(row, now, 7, 11, "last_daily_message_date", "next_daily_message_at")

    assert due is False
    target = datetime.fromisoformat(update["next_daily_message_at"])
    window_start = datetime.combine(today_local, datetime.min.time(), tzinfo=timezone.utc).replace(hour=7)
    window_end = datetime.combine(today_local, datetime.min.time(), tzinfo=timezone.utc).replace(hour=11)
    assert window_start <= target < window_end
    assert target >= now


def test_window_already_passed_today_sets_no_target():
    now = datetime.now(timezone.utc).replace(hour=23, minute=0, second=0, microsecond=0)
    row = _base_row()

    due, update, _ = _due_check(row, now, 7, 11, "last_daily_message_date", "next_daily_message_at")
    assert due is False
    assert update is None


def test_due_once_target_has_passed():
    now = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    target = now - timedelta(minutes=5)
    row = _base_row(next_daily_message_at=target.isoformat())

    due, update, today_local = _due_check(row, now, 7, 11, "last_daily_message_date", "next_daily_message_at")

    assert due is True
    assert update == {"last_daily_message_date": today_local.isoformat(), "next_daily_message_at": None}


def test_target_not_yet_reached_does_nothing():
    now = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    target = now + timedelta(minutes=30)
    row = _base_row(next_daily_message_at=target.isoformat())

    due, update, _ = _due_check(row, now, 7, 11, "last_daily_message_date", "next_daily_message_at")
    assert due is False
    assert update is None


def test_stale_target_from_a_previous_day_is_recomputed():
    now = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    stale_target = now - timedelta(days=1)
    row = _base_row(next_daily_message_at=stale_target.isoformat())

    due, update, _ = _due_check(row, now, 7, 11, "last_daily_message_date", "next_daily_message_at")

    assert due is False
    new_target = datetime.fromisoformat(update["next_daily_message_at"])
    assert new_target > now


def test_positive_offset_uses_local_window_not_utc():
    # 04:00 UTC with a +180 min offset (e.g. East Africa) is 07:00
    # local -- inside a 7-11 window even though it's early in UTC.
    now = datetime.now(timezone.utc).replace(hour=4, minute=0, second=0, microsecond=0)
    row = _base_row(last_known_utc_offset_minutes=180)

    due, update, _ = _due_check(row, now, 7, 11, "last_daily_message_date", "next_daily_message_at")
    assert update is not None  # a target was set, not skipped as outside the window


def test_morning_and_nightly_use_independent_keys():
    # A user already credited for today's morning check-in must
    # still be independently evaluated for tonight's recap.
    now = datetime.now(timezone.utc).replace(hour=21, minute=0, second=0, microsecond=0)
    today = now.date()
    row = _base_row(last_daily_message_date=today.isoformat())

    due, update, _ = _due_check(row, now, 20, 23, "last_nightly_recap_date", "next_nightly_recap_at")
    assert update is not None  # recap's own tracking is untouched by morning's


# ---- _process_morning_checkin ----

def test_morning_checkin_sends_when_due():
    now = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    target = now - timedelta(minutes=5)
    row = _base_row(next_daily_message_at=target.isoformat())

    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message.NotificationService") as mock_notif, \
         patch("daily_message._generate_morning_checkin", return_value="morning! you said you had that test today"):
        _process_morning_checkin(row, now)

        mock_notif.create_notification.assert_called_once_with(
            user_id="user-1", title="Ollie", body="morning! you said you had that test today",
        )
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["last_daily_message_date"] == now.date().isoformat()


def test_morning_checkin_does_not_send_before_due():
    now = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)
    row = _base_row()

    with patch("daily_message.supabase"), \
         patch("daily_message.NotificationService") as mock_notif:
        _process_morning_checkin(row, now)
        mock_notif.create_notification.assert_not_called()


# ---- _process_nightly_recap ----

def test_nightly_recap_sends_when_due_and_content_present():
    now = datetime.now(timezone.utc).replace(hour=21, minute=0, second=0, microsecond=0)
    target = now - timedelta(minutes=5)
    row = _base_row(next_nightly_recap_at=target.isoformat())

    with patch("daily_message.supabase"), \
         patch("daily_message.NotificationService") as mock_notif, \
         patch("daily_message._generate_nightly_recap", return_value="- finished your project section"):
        _process_nightly_recap(row, now)

        mock_notif.create_notification.assert_called_once_with(
            user_id="user-1", title="Today with Ollie", body="- finished your project section",
        )


def test_nightly_recap_skips_notification_when_nothing_happened():
    # Recap generation returning None (nothing to report) must NOT
    # send a notification at all -- never a generic filler.
    now = datetime.now(timezone.utc).replace(hour=21, minute=0, second=0, microsecond=0)
    target = now - timedelta(minutes=5)
    row = _base_row(next_nightly_recap_at=target.isoformat())

    with patch("daily_message.supabase"), \
         patch("daily_message.NotificationService") as mock_notif, \
         patch("daily_message._generate_nightly_recap", return_value=None):
        _process_nightly_recap(row, now)
        mock_notif.create_notification.assert_not_called()


# ---- run_daily_messages (sweep) ----

def test_run_daily_messages_processes_both_moments_per_user():
    row = _base_row()
    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message._process_morning_checkin") as mock_morning, \
         patch("daily_message._process_nightly_recap") as mock_nightly:
        mock_supabase.table.return_value.select.return_value.not_.is_.return_value.not_.is_.return_value.execute.return_value = \
            MagicMock(data=[row])
        run_daily_messages()

        mock_morning.assert_called_once()
        mock_nightly.assert_called_once()


def test_run_daily_messages_skips_users_with_notifications_disabled():
    row = _base_row(notifications_enabled=False)
    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message._process_morning_checkin") as mock_morning, \
         patch("daily_message._process_nightly_recap") as mock_nightly:
        mock_supabase.table.return_value.select.return_value.not_.is_.return_value.not_.is_.return_value.execute.return_value = \
            MagicMock(data=[row])
        run_daily_messages()

        mock_morning.assert_not_called()
        mock_nightly.assert_not_called()


def test_run_daily_messages_one_users_failure_does_not_block_another():
    rows = [_base_row(id="user-1"), _base_row(id="user-2")]
    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message._process_morning_checkin", side_effect=[Exception("boom"), None]), \
         patch("daily_message._process_nightly_recap") as mock_nightly:
        mock_supabase.table.return_value.select.return_value.not_.is_.return_value.not_.is_.return_value.execute.return_value = \
            MagicMock(data=rows)
        run_daily_messages()  # must not raise
        assert mock_nightly.call_count == 2


# ---- _generate_morning_checkin ----

def test_morning_checkin_generation_failure_falls_back_to_a_template_line():
    with patch("daily_message.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.get_relevant_memories.side_effect = Exception("boom")
        result = daily_message._generate_morning_checkin("user-1", datetime.now(timezone.utc).date())
        assert result in daily_message.FALLBACK_LINES


def test_morning_checkin_flagged_generation_falls_back_to_a_template_line():
    with patch("daily_message.OllieDB") as mock_db_cls, \
         patch("daily_message.build_memory_context", return_value="MEMORY: likes hiking"), \
         patch("daily_message.build_interest_context", return_value=""), \
         patch("daily_message.openai_client") as mock_client, \
         patch("daily_message.moderate_text", return_value={"categories": ["x"]}):
        mock_db_cls.return_value.get_relevant_memories.return_value = []
        mock_db_cls.return_value.get_user_context.return_value = {}
        mock_db_cls.return_value.get_memories_by_category.return_value = []
        mock_db_cls.return_value.get_mood_for_date.return_value = None
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="something flagged"))
        ]

        result = daily_message._generate_morning_checkin("user-1", datetime.now(timezone.utc).date())
        assert result in daily_message.FALLBACK_LINES


def test_morning_checkin_prioritizes_recent_event_memory():
    with patch("daily_message.OllieDB") as mock_db_cls, \
         patch("daily_message.build_memory_context", return_value=""), \
         patch("daily_message.build_interest_context", return_value=""), \
         patch("daily_message.openai_client") as mock_client, \
         patch("daily_message.moderate_text", return_value=None):
        mock_db_cls.return_value.get_relevant_memories.return_value = []
        mock_db_cls.return_value.get_user_context.return_value = {}
        mock_db_cls.return_value.get_memories_by_category.return_value = [
            {"memory_text": "Has a test today", "category": "event"}
        ]
        mock_db_cls.return_value.get_mood_for_date.return_value = None
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="morning! you said you had that test today"))
        ]

        result = daily_message._generate_morning_checkin("user-1", datetime.now(timezone.utc).date())
        assert result == "morning! you said you had that test today"

        prompt = mock_client.chat.completions.create.call_args[1]["messages"][0]["content"]
        assert "RECENTLY MENTIONED, MIGHT BE TODAY: Has a test today" in prompt


def test_morning_checkin_no_context_at_all_falls_back():
    with patch("daily_message.OllieDB") as mock_db_cls, \
         patch("daily_message.build_memory_context", return_value=""), \
         patch("daily_message.build_interest_context", return_value=""):
        mock_db_cls.return_value.get_relevant_memories.return_value = []
        mock_db_cls.return_value.get_user_context.return_value = {}
        mock_db_cls.return_value.get_memories_by_category.return_value = []
        mock_db_cls.return_value.get_mood_for_date.return_value = None

        result = daily_message._generate_morning_checkin("user-1", datetime.now(timezone.utc).date())
        assert result in daily_message.FALLBACK_LINES


# ---- _generate_nightly_recap ----

def test_nightly_recap_returns_none_with_fewer_than_two_messages():
    with patch("daily_message.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.get_messages_since.return_value = [{"sender": "user", "message": "hi"}]
        result = daily_message._generate_nightly_recap("user-1", datetime.now(timezone.utc).date(), timedelta(0))
        assert result is None


def test_nightly_recap_returns_none_when_model_says_nothing():
    with patch("daily_message.OllieDB") as mock_db_cls, \
         patch("daily_message.openai_client") as mock_client:
        mock_db_cls.return_value.get_messages_since.return_value = [
            {"sender": "user", "message": "hey"}, {"sender": "ollie", "message": "hey!"},
        ]
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="NOTHING"))
        ]
        result = daily_message._generate_nightly_recap("user-1", datetime.now(timezone.utc).date(), timedelta(0))
        assert result is None


def test_nightly_recap_returns_content_grounded_in_transcript():
    with patch("daily_message.OllieDB") as mock_db_cls, \
         patch("daily_message.openai_client") as mock_client, \
         patch("daily_message.moderate_text", return_value=None):
        mock_db_cls.return_value.get_messages_since.return_value = [
            {"sender": "user", "message": "finished my project section today"},
            {"sender": "ollie", "message": "that's huge!"},
        ]
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="- finished your project section"))
        ]
        result = daily_message._generate_nightly_recap("user-1", datetime.now(timezone.utc).date(), timedelta(0))
        assert result == "- finished your project section"

        prompt = mock_client.chat.completions.create.call_args[1]["messages"][0]["content"]
        assert "finished my project section today" in prompt


def test_nightly_recap_flagged_content_returns_none():
    with patch("daily_message.OllieDB") as mock_db_cls, \
         patch("daily_message.openai_client") as mock_client, \
         patch("daily_message.moderate_text", return_value={"categories": ["x"]}):
        mock_db_cls.return_value.get_messages_since.return_value = [
            {"sender": "user", "message": "hey"}, {"sender": "ollie", "message": "hey!"},
        ]
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="some recap"))
        ]
        result = daily_message._generate_nightly_recap("user-1", datetime.now(timezone.utc).date(), timedelta(0))
        assert result is None


def test_nightly_recap_generation_failure_returns_none_not_raises():
    with patch("daily_message.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.get_messages_since.side_effect = Exception("boom")
        result = daily_message._generate_nightly_recap("user-1", datetime.now(timezone.utc).date(), timedelta(0))
        assert result is None


def test_morning_window_constants_read_as_morning():
    # Sanity guard against accidentally reverting to the old
    # all-day 8am-9pm window this replaced.
    assert 5 <= MORNING_WINDOW_START_HOUR <= 9
    assert 9 <= MORNING_WINDOW_END_HOUR <= 12
