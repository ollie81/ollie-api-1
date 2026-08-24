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


# ============================================================
# mark_as_read / delete_notification -- both scoped to user_id so
# one user can't act on another user's notification by
# guessing/enumerating an id (previously unscoped -- an IDOR gap).
# ============================================================

def _mock_update_result(mock_supabase, data):
    result = MagicMock()
    result.data = data
    mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = result


def _mock_delete_result(mock_supabase, data):
    result = MagicMock()
    result.data = data
    mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value = result


def test_mark_as_read_scopes_to_user_id_and_reports_success():
    with patch("notification_service.supabase") as mock_supabase:
        _mock_update_result(mock_supabase, [{"id": "notif-1"}])

        assert NotificationService.mark_as_read("notif-1", "user-1") is True

        mock_supabase.table.return_value.update.return_value.eq.assert_called_with("id", "notif-1")
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.assert_called_with("user_id", "user-1")


def test_mark_as_read_returns_false_for_someone_elses_notification():
    with patch("notification_service.supabase") as mock_supabase:
        _mock_update_result(mock_supabase, [])  # id exists, but not owned by this user -- no row matched

        assert NotificationService.mark_as_read("notif-1", "user-2") is False


def test_delete_notification_scopes_to_user_id_and_reports_success():
    with patch("notification_service.supabase") as mock_supabase:
        _mock_delete_result(mock_supabase, [{"id": "notif-1"}])
        assert NotificationService.delete_notification("notif-1", "user-1") is True


def test_delete_notification_returns_false_for_someone_elses_notification():
    with patch("notification_service.supabase") as mock_supabase:
        _mock_delete_result(mock_supabase, [])
        assert NotificationService.delete_notification("notif-1", "user-2") is False
