# ============================================================
# Tests for auth._cancel_pending_deletion_if_needed and its wiring
# into all three login routes (phone, email, Google) -- logging back
# in during the grace period cancels a pending account deletion (see
# test_account_deletion_db.py / test_settings_delete_account.py for
# how that grace period starts and is eventually carried out).
# ============================================================

from unittest.mock import patch, MagicMock

from starlette.requests import Request

from auth import (
    _cancel_pending_deletion_if_needed,
    login,
    email_login,
    google_login,
    AuthRequest,
    EmailAuthRequest,
    GoogleAuthRequest,
)


def _fake_request():
    return Request(scope={
        "type": "http", "method": "POST", "path": "/",
        "headers": [], "client": ("testclient", 123), "query_string": b"",
    })


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


def test_cancel_helper_clears_and_reports_true_when_pending():
    with patch("auth.supabase") as mock_supabase:
        cancelled = _cancel_pending_deletion_if_needed(
            {"id": "user-1", "deletion_requested_at": "2026-08-20T00:00:00+00:00"}
        )

        assert cancelled is True
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call == {"deletion_requested_at": None}
        mock_supabase.table.return_value.update.return_value.eq.assert_called_once_with("id", "user-1")


def test_cancel_helper_is_a_noop_when_nothing_pending():
    with patch("auth.supabase") as mock_supabase:
        cancelled = _cancel_pending_deletion_if_needed({"id": "user-1", "deletion_requested_at": None})

        assert cancelled is False
        mock_supabase.table.assert_not_called()


def test_phone_login_reports_deletion_cancelled():
    with patch("auth.supabase") as mock_supabase, \
         patch("auth.verify_password", return_value=True):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = _mock_result(
            [{"id": "user-1", "password_hash": "x", "deletion_requested_at": "2026-08-20T00:00:00+00:00"}]
        )

        result = login(AuthRequest(phone_number="+15551234567", password="secret"), _fake_request())

        assert result["deletion_cancelled"] is True


def test_phone_login_reports_false_when_nothing_pending():
    with patch("auth.supabase") as mock_supabase, \
         patch("auth.verify_password", return_value=True):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = _mock_result(
            [{"id": "user-1", "password_hash": "x", "deletion_requested_at": None}]
        )

        result = login(AuthRequest(phone_number="+15551234567", password="secret"), _fake_request())

        assert result["deletion_cancelled"] is False


def test_email_login_reports_deletion_cancelled():
    with patch("auth.supabase") as mock_supabase, \
         patch("auth.verify_password", return_value=True):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = _mock_result(
            [{"id": "user-1", "password_hash": "x", "deletion_requested_at": "2026-08-20T00:00:00+00:00"}]
        )

        result = email_login(EmailAuthRequest(email="a@example.com", password="secret"), _fake_request())

        assert result["deletion_cancelled"] is True


def test_google_login_cancels_deletion_for_a_returning_user():
    with patch("auth.id_token.verify_oauth2_token", return_value={"email": "a@example.com", "name": "A"}), \
         patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = _mock_result(
            [{"id": "user-1", "username": "A", "deletion_requested_at": "2026-08-20T00:00:00+00:00"}]
        )

        result = google_login(GoogleAuthRequest(id_token="fake-token"), _fake_request())

        assert result["deletion_cancelled"] is True


def test_google_login_new_user_reports_false_without_an_update_call():
    with patch("auth.id_token.verify_oauth2_token", return_value={"email": "new@example.com", "name": "New"}), \
         patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = _mock_result([])
        mock_supabase.table.return_value.insert.return_value.execute.return_value = _mock_result(
            [{"id": "user-1", "username": "New"}]
        )

        result = google_login(GoogleAuthRequest(id_token="fake-token"), _fake_request())

        assert result["deletion_cancelled"] is False
        mock_supabase.table.return_value.update.assert_not_called()
