# ============================================================
# Tests for NotificationService.create_notification -- in
# particular that it now respects notifications_enabled (it
# previously ignored it entirely, so toggling notifications off
# in Settings had no effect on whether a push actually went out).
# ============================================================

from unittest.mock import patch, MagicMock

from notification_service import NotificationService


def _mock_user_row(data):
    result = MagicMock()
    result.data = data
    return result


def _patch_user(mock_supabase, row):
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = \
        _mock_user_row(row)


def test_push_sent_when_enabled_is_true():
    with patch("notification_service.supabase") as mock_supabase, \
         patch("notification_service.send_push") as mock_send_push:
        _patch_user(mock_supabase, {"fcm_token": "tok-1", "notifications_enabled": True})

        NotificationService.create_notification("user-1", "title", "body")

        mock_send_push.assert_called_once_with("tok-1", "title", "body")


def test_push_sent_when_enabled_is_unset():
    # Existing users who've never touched the Settings toggle have
    # no value here at all -- must default to sending, not silently
    # going dark for everyone who signed up before the toggle existed.
    with patch("notification_service.supabase") as mock_supabase, \
         patch("notification_service.send_push") as mock_send_push:
        _patch_user(mock_supabase, {"fcm_token": "tok-1", "notifications_enabled": None})

        NotificationService.create_notification("user-1", "title", "body")

        mock_send_push.assert_called_once_with("tok-1", "title", "body")


def test_push_suppressed_when_explicitly_disabled():
    with patch("notification_service.supabase") as mock_supabase, \
         patch("notification_service.send_push") as mock_send_push:
        _patch_user(mock_supabase, {"fcm_token": "tok-1", "notifications_enabled": False})

        NotificationService.create_notification("user-1", "title", "body")

        mock_send_push.assert_not_called()


def test_notification_row_still_saved_when_push_disabled():
    # The toggle should only gate the push, not the in-app history.
    with patch("notification_service.supabase") as mock_supabase, \
         patch("notification_service.send_push"):
        _patch_user(mock_supabase, {"fcm_token": "tok-1", "notifications_enabled": False})

        NotificationService.create_notification("user-1", "title", "body")

        mock_supabase.table.return_value.insert.assert_called_once()


def test_no_token_never_calls_send_push():
    with patch("notification_service.supabase") as mock_supabase, \
         patch("notification_service.send_push") as mock_send_push:
        _patch_user(mock_supabase, {"fcm_token": None, "notifications_enabled": True})

        NotificationService.create_notification("user-1", "title", "body")

        mock_send_push.assert_not_called()
