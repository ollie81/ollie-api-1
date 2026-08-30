# ============================================================
# Tests for event_scheduler._proactive_notifications_off and its
# wiring into run_due_notifications -- Phase 5's Off/Low/Normal/
# Frequent control gating Ollie-INITIATED check-ins. Reminders
# (explicit user requests) are untouched -- see
# test_personalize_reminder.py's existing reminder-kind coverage.
# ============================================================

from unittest.mock import patch, MagicMock

from event_scheduler import _proactive_notifications_off, run_due_notifications


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


# ---- _proactive_notifications_off ----

def test_returns_true_when_frequency_is_off():
    with patch("event_scheduler.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = \
            _mock_result({"notification_frequency": "off"})
        assert _proactive_notifications_off("user-1") is True


def test_returns_false_for_other_frequencies():
    with patch("event_scheduler.supabase") as mock_supabase:
        for freq in ("low", "normal", "frequent"):
            mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = \
                _mock_result({"notification_frequency": freq})
            assert _proactive_notifications_off("user-1") is False


def test_fails_open_on_lookup_error():
    with patch("event_scheduler.supabase") as mock_supabase:
        mock_supabase.table.side_effect = Exception("db down")
        assert _proactive_notifications_off("user-1") is False


# ---- run_due_notifications: checkin gated, reminder never gated ----

def test_checkin_skipped_and_marked_skipped_when_frequency_off():
    with patch("event_scheduler.supabase") as mock_supabase, \
         patch("event_scheduler.NotificationService") as mock_notif, \
         patch("event_scheduler._proactive_notifications_off", return_value=True):
        due_result = MagicMock()
        due_result.data = [{
            "id": "evt-1", "user_id": "user-1", "kind": "checkin",
            "event_summary": "the interview",
        }]
        mock_supabase.table.return_value.select.return_value.eq.return_value.lte.return_value.execute.return_value = due_result

        run_due_notifications()

        mock_notif.create_notification.assert_not_called()
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call == {"status": "skipped"}


def test_checkin_sends_when_frequency_not_off():
    with patch("event_scheduler.supabase") as mock_supabase, \
         patch("event_scheduler.NotificationService") as mock_notif, \
         patch("event_scheduler._proactive_notifications_off", return_value=False), \
         patch("event_scheduler._checkins_sent_today", return_value=0):
        due_result = MagicMock()
        due_result.data = [{
            "id": "evt-1", "user_id": "user-1", "kind": "checkin",
            "event_summary": "the interview",
        }]
        mock_supabase.table.return_value.select.return_value.eq.return_value.lte.return_value.execute.return_value = due_result

        run_due_notifications()

        mock_notif.create_notification.assert_called_once()


def test_reminder_kind_never_checks_frequency():
    # Explicit reminders always send -- _proactive_notifications_off
    # must not even be consulted for kind == "reminder".
    with patch("event_scheduler.supabase") as mock_supabase, \
         patch("event_scheduler.NotificationService") as mock_notif, \
         patch("event_scheduler._proactive_notifications_off") as mock_off:
        due_result = MagicMock()
        due_result.data = [{
            "id": "evt-1", "user_id": "user-1", "kind": "reminder",
            "event_summary": "drink water", "notification_body": "drink water!",
        }]
        mock_supabase.table.return_value.select.return_value.eq.return_value.lte.return_value.execute.return_value = due_result

        run_due_notifications()

        mock_off.assert_not_called()
        mock_notif.create_notification.assert_called_once()
