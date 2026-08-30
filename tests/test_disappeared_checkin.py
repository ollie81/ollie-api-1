# ============================================================
# Tests for daily_message._process_disappeared_checkin /
# _generate_disappeared_checkin, and the frequency gating wired
# into run_daily_messages (Phase 5).
# ============================================================

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import daily_message
from daily_message import _process_disappeared_checkin, run_daily_messages


def _base_row(**overrides):
    row = {
        "id": "user-1",
        "last_known_utc_offset_minutes": 0,
        "last_daily_message_date": None,
        "next_daily_message_at": None,
        "last_nightly_recap_date": None,
        "next_nightly_recap_at": None,
        "notification_frequency": "normal",
        "last_message_at": None,
        "last_disappeared_checkin_at": None,
    }
    row.update(overrides)
    return row


# ---- _process_disappeared_checkin ----

def test_never_talked_to_ollie_is_skipped():
    now = datetime.now(timezone.utc)
    row = _base_row(last_message_at=None)

    with patch("daily_message.supabase"), \
         patch("daily_message.NotificationService") as mock_notif:
        _process_disappeared_checkin(row, now, "normal")
        mock_notif.create_notification.assert_not_called()


def test_recently_active_user_is_skipped():
    now = datetime.now(timezone.utc)
    row = _base_row(last_message_at=(now - timedelta(days=1)).isoformat())

    with patch("daily_message.supabase"), \
         patch("daily_message.NotificationService") as mock_notif:
        _process_disappeared_checkin(row, now, "normal")
        mock_notif.create_notification.assert_not_called()


def test_silent_past_threshold_sends_checkin():
    now = datetime.now(timezone.utc)
    row = _base_row(last_message_at=(now - timedelta(days=6)).isoformat())

    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message.NotificationService") as mock_notif, \
         patch("daily_message._generate_disappeared_checkin", return_value="you disappeared 😂 everything good?"):
        _process_disappeared_checkin(row, now, "normal")

        mock_notif.create_notification.assert_called_once_with(
            user_id="user-1", title="Ollie", body="you disappeared 😂 everything good?",
        )
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert "last_disappeared_checkin_at" in update_call


def test_never_sends_twice_in_a_row():
    now = datetime.now(timezone.utc)
    last_message = now - timedelta(days=10)
    last_checkin_after_message = last_message + timedelta(days=1)  # already checked in since they last talked
    row = _base_row(
        last_message_at=last_message.isoformat(),
        last_disappeared_checkin_at=last_checkin_after_message.isoformat(),
    )

    with patch("daily_message.supabase"), \
         patch("daily_message.NotificationService") as mock_notif:
        _process_disappeared_checkin(row, now, "normal")
        mock_notif.create_notification.assert_not_called()


def test_new_silence_after_they_returned_can_send_again():
    # They talked again AFTER the last check-in, then went quiet
    # again -- must be eligible for a new check-in.
    now = datetime.now(timezone.utc)
    old_checkin = now - timedelta(days=20)
    new_message = now - timedelta(days=15)  # they came back after the old check-in
    row = _base_row(
        last_message_at=new_message.isoformat(),
        last_disappeared_checkin_at=old_checkin.isoformat(),
    )

    with patch("daily_message.supabase"), \
         patch("daily_message.NotificationService") as mock_notif, \
         patch("daily_message._generate_disappeared_checkin", return_value="hey stranger"):
        _process_disappeared_checkin(row, now, "normal")
        mock_notif.create_notification.assert_called_once()


def test_frequency_changes_the_threshold():
    now = datetime.now(timezone.utc)
    row = _base_row(last_message_at=(now - timedelta(days=4)).isoformat())

    with patch("daily_message.supabase"), \
         patch("daily_message.NotificationService") as mock_notif, \
         patch("daily_message._generate_disappeared_checkin", return_value="hey"):
        # 4 days silent: not enough for 'low' (10) or 'normal' (5), but enough for 'frequent' (3).
        _process_disappeared_checkin(row, now, "low")
        mock_notif.create_notification.assert_not_called()

        _process_disappeared_checkin(row, now, "normal")
        mock_notif.create_notification.assert_not_called()

        _process_disappeared_checkin(row, now, "frequent")
        mock_notif.create_notification.assert_called_once()


# ---- _generate_disappeared_checkin ----

def test_generation_no_memories_falls_back():
    with patch("daily_message.OllieDB") as mock_db_cls, \
         patch("daily_message.build_memory_context", return_value=""):
        mock_db_cls.return_value.get_relevant_memories.return_value = []
        mock_db_cls.return_value.get_user_context.return_value = {}
        result = daily_message._generate_disappeared_checkin("user-1")
        assert result in daily_message.DISAPPEARED_FALLBACK_LINES


def test_generation_failure_falls_back():
    with patch("daily_message.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.get_relevant_memories.side_effect = Exception("boom")
        result = daily_message._generate_disappeared_checkin("user-1")
        assert result in daily_message.DISAPPEARED_FALLBACK_LINES


def test_flagged_generation_falls_back():
    with patch("daily_message.OllieDB") as mock_db_cls, \
         patch("daily_message.build_memory_context", return_value="MEMORY: likes hiking"), \
         patch("daily_message.openai_client") as mock_client, \
         patch("daily_message.moderate_text", return_value={"categories": ["x"]}):
        mock_db_cls.return_value.get_relevant_memories.return_value = []
        mock_db_cls.return_value.get_user_context.return_value = {}
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="something flagged"))
        ]
        result = daily_message._generate_disappeared_checkin("user-1")
        assert result in daily_message.DISAPPEARED_FALLBACK_LINES


# ---- run_daily_messages: frequency gating across all three moments ----

def test_off_frequency_skips_everything():
    row = _base_row(notification_frequency="off")
    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message._process_morning_checkin") as mock_morning, \
         patch("daily_message._process_nightly_recap") as mock_nightly, \
         patch("daily_message._process_disappeared_checkin") as mock_disappeared:
        mock_supabase.table.return_value.select.return_value.not_.is_.return_value.not_.is_.return_value.execute.return_value = \
            MagicMock(data=[row])
        run_daily_messages()

        mock_morning.assert_not_called()
        mock_nightly.assert_not_called()
        mock_disappeared.assert_not_called()


def test_low_frequency_only_processes_morning():
    row = _base_row(notification_frequency="low")
    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message._process_morning_checkin") as mock_morning, \
         patch("daily_message._process_nightly_recap") as mock_nightly, \
         patch("daily_message._process_disappeared_checkin") as mock_disappeared:
        mock_supabase.table.return_value.select.return_value.not_.is_.return_value.not_.is_.return_value.execute.return_value = \
            MagicMock(data=[row])
        run_daily_messages()

        mock_morning.assert_called_once()
        mock_nightly.assert_not_called()
        mock_disappeared.assert_not_called()


def test_normal_frequency_processes_all_three():
    row = _base_row(notification_frequency="normal")
    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message._process_morning_checkin") as mock_morning, \
         patch("daily_message._process_nightly_recap") as mock_nightly, \
         patch("daily_message._process_disappeared_checkin") as mock_disappeared:
        mock_supabase.table.return_value.select.return_value.not_.is_.return_value.not_.is_.return_value.execute.return_value = \
            MagicMock(data=[row])
        run_daily_messages()

        mock_morning.assert_called_once()
        mock_nightly.assert_called_once()
        mock_disappeared.assert_called_once()


def test_missing_frequency_defaults_to_normal_behavior():
    row = _base_row()
    del row["notification_frequency"]
    with patch("daily_message.supabase") as mock_supabase, \
         patch("daily_message._process_morning_checkin") as mock_morning, \
         patch("daily_message._process_nightly_recap") as mock_nightly, \
         patch("daily_message._process_disappeared_checkin") as mock_disappeared:
        mock_supabase.table.return_value.select.return_value.not_.is_.return_value.not_.is_.return_value.execute.return_value = \
            MagicMock(data=[row])
        run_daily_messages()

        mock_morning.assert_called_once()
        mock_nightly.assert_called_once()
        mock_disappeared.assert_called_once()
