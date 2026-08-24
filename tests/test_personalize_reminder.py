# ============================================================
# Tests for event_scheduler._personalize_reminder and its wiring
# into maybe_schedule_reminder / run_due_notifications.
#
# Real bug report: a reminder notification just said the bare
# extracted text ("drink water"), none of Ollie's actual voice --
# not what the user asked Ollie to say as "a friend". Generation
# happens once at schedule time (not in the send-time sweep) and
# is stored in notification_body, with a safe templated fallback
# if generation or moderation ever has an issue.
# ============================================================

from unittest.mock import patch, MagicMock

from event_scheduler import _personalize_reminder, maybe_schedule_reminder, run_due_notifications


def test_successful_generation_is_used_as_is(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion("hey! don't forget to drink some water 💧")), \
         patch("event_scheduler.moderate_text", return_value=None):
        result = _personalize_reminder("drink water")

    assert result == "hey! don't forget to drink some water 💧"


def test_empty_generation_falls_back_to_template(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion(None)), \
         patch("event_scheduler.moderate_text", return_value=None):
        result = _personalize_reminder("drink water")

    assert result == "hey — don't forget: drink water"


def test_flagged_generation_falls_back_to_template(mock_chat_completion):
    with patch("event_scheduler.openai_client.chat.completions.create",
               return_value=mock_chat_completion("something flagged")), \
         patch("event_scheduler.moderate_text", return_value={"flagged": True, "categories": ["x"]}):
        result = _personalize_reminder("drink water")

    assert result == "hey — don't forget: drink water"


def test_generation_exception_falls_back_to_template():
    with patch("event_scheduler.openai_client.chat.completions.create", side_effect=Exception("timeout")):
        result = _personalize_reminder("drink water")

    assert result == "hey — don't forget: drink water"


def test_maybe_schedule_reminder_stores_personalized_body(mock_chat_completion):
    reminder_json = (
        '{"is_reminder": true, "reminder_text": "drink water", "time_type": "relative", '
        '"relative_minutes": 10, "absolute_hour": null, "absolute_minute": null, "days_from_today": null}'
    )
    with patch("event_scheduler.openai_client.chat.completions.create") as mock_create, \
         patch("event_scheduler.moderate_text", return_value=None), \
         patch("event_scheduler.supabase") as mock_supabase:
        mock_create.side_effect = [
            mock_chat_completion(reminder_json),                                  # detection call
            mock_chat_completion("hey friend, time to drink some water 💧"),      # personalization call
        ]
        mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock()

        maybe_schedule_reminder("user-1", "remind me to drink water in 10 minutes", utc_offset_minutes=0)

        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert insert_call["event_summary"] == "drink water"
        assert insert_call["notification_body"] == "hey friend, time to drink some water 💧"


def test_run_due_notifications_uses_stored_notification_body():
    with patch("event_scheduler.supabase") as mock_supabase, \
         patch("event_scheduler.NotificationService") as mock_notif:
        due_result = MagicMock()
        due_result.data = [{
            "id": "evt-1", "user_id": "user-1", "kind": "reminder",
            "event_summary": "drink water",
            "notification_body": "hey friend, don't forget to hydrate 💧",
        }]
        mock_supabase.table.return_value.select.return_value.eq.return_value.lte.return_value.execute.return_value = due_result

        run_due_notifications()

        mock_notif.create_notification.assert_called_once_with(
            user_id="user-1", title="Ollie reminding you", body="hey friend, don't forget to hydrate 💧",
        )


def test_run_due_notifications_falls_back_to_event_summary_when_body_missing():
    # Rows scheduled before notification_body existed won't have it set.
    with patch("event_scheduler.supabase") as mock_supabase, \
         patch("event_scheduler.NotificationService") as mock_notif:
        due_result = MagicMock()
        due_result.data = [{
            "id": "evt-1", "user_id": "user-1", "kind": "reminder",
            "event_summary": "drink water",
            "notification_body": None,
        }]
        mock_supabase.table.return_value.select.return_value.eq.return_value.lte.return_value.execute.return_value = due_result

        run_due_notifications()

        mock_notif.create_notification.assert_called_once_with(
            user_id="user-1", title="Ollie reminding you", body="drink water",
        )
