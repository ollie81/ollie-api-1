# ============================================================
# Tests for the /auth/email/* routes -- a third sign-in method
# alongside phone and Google. Same direct-route-call style as
# test_auth_routes.py, including the real minimal starlette
# Request the @limiter.limit decorator requires.
#
# Focus: the account is never created before the OTP is verified
# (so an abandoned signup can't squat an email), a wrong/expired/
# reused code is rejected, login doesn't leak whether an account
# exists, and password reset actually invalidates existing sessions.
# ============================================================

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from auth import (
    request_email_signup_otp,
    email_signup,
    email_login,
    email_forgot_password,
    email_reset_password,
    EmailSignupOtpRequest,
    EmailSignupRequest,
    EmailAuthRequest,
    EmailForgotRequest,
    EmailResetRequest,
    hash_password,
    _hash_otp,
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


# ---- /email/signup/request-otp ----

def test_request_otp_for_new_email_sends_code_and_stages_it():
    with patch("auth.supabase") as mock_supabase, \
         patch("auth.send_otp_email", return_value=True) as mock_send:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])

        result = request_email_signup_otp(EmailSignupOtpRequest(email="New@Example.com"), _fake_request())

        assert result["success"] is True
        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == "new@example.com"  # normalized lowercase
        upsert_call = mock_supabase.table.return_value.upsert.call_args[0][0]
        assert upsert_call["email"] == "new@example.com"
        assert "otp_hash" in upsert_call and "expires_at" in upsert_call


def test_request_otp_rejects_already_registered_email():
    with patch("auth.supabase") as mock_supabase, \
         patch("auth.send_otp_email") as mock_send:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "user-1"}])

        with pytest.raises(HTTPException) as exc_info:
            request_email_signup_otp(EmailSignupOtpRequest(email="taken@example.com"), _fake_request())
        assert exc_info.value.status_code == 400
        mock_send.assert_not_called()


def test_request_otp_rejects_malformed_email():
    with patch("auth.supabase") as mock_supabase, patch("auth.send_otp_email") as mock_send:
        with pytest.raises(HTTPException) as exc_info:
            request_email_signup_otp(EmailSignupOtpRequest(email="not-an-email"), _fake_request())
        assert exc_info.value.status_code == 400
        mock_send.assert_not_called()
        mock_supabase.table.assert_not_called()


def test_request_otp_send_failure_does_not_stage_anything():
    with patch("auth.supabase") as mock_supabase, \
         patch("auth.send_otp_email", return_value=False):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])

        with pytest.raises(HTTPException) as exc_info:
            request_email_signup_otp(EmailSignupOtpRequest(email="new@example.com"), _fake_request())
        assert exc_info.value.status_code == 500
        mock_supabase.table.return_value.upsert.assert_not_called()


# ---- /email/signup ----

def _signup_req(otp="123456", email="new@example.com", password="hunters2", dob=None):
    return EmailSignupRequest(email=email, password=password, otp=otp, date_of_birth=dob)


def test_signup_with_correct_code_creates_account_and_issues_tokens():
    future = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            _mock_result([]),  # no existing user
            _mock_result([{"email": "new@example.com", "otp_hash": _hash_otp("123456"), "expires_at": future}]),
        ]
        mock_supabase.table.return_value.insert.return_value.execute.return_value = \
            _mock_result([{"id": "user-1"}])

        result = email_signup(_signup_req(), _fake_request())

        assert result["success"] is True
        assert "access_token" in result and "refresh_token" in result
        # First insert call is the users row; the second (not checked
        # here) is the refresh_tokens row -- both go through the same
        # mocked chain since the mock doesn't distinguish table names.
        insert_call = mock_supabase.table.return_value.insert.call_args_list[0][0][0]
        assert insert_call["email"] == "new@example.com"
        assert insert_call["email_verified"] is True
        # Pending OTP row is cleaned up once consumed.
        mock_supabase.table.return_value.delete.return_value.eq.assert_called_once()


def test_signup_with_wrong_code_is_rejected_and_account_not_created():
    future = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            _mock_result([]),
            _mock_result([{"email": "new@example.com", "otp_hash": _hash_otp("123456"), "expires_at": future}]),
        ]

        with pytest.raises(HTTPException) as exc_info:
            email_signup(_signup_req(otp="000000"), _fake_request())
        assert exc_info.value.status_code == 400
        mock_supabase.table.return_value.insert.assert_not_called()


def test_signup_with_expired_code_is_rejected_and_cleaned_up():
    past = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            _mock_result([]),
            _mock_result([{"email": "new@example.com", "otp_hash": _hash_otp("123456"), "expires_at": past}]),
        ]

        with pytest.raises(HTTPException) as exc_info:
            email_signup(_signup_req(), _fake_request())
        assert exc_info.value.status_code == 400
        mock_supabase.table.return_value.insert.assert_not_called()
        mock_supabase.table.return_value.delete.return_value.eq.assert_called_once()


def test_signup_with_no_pending_otp_is_rejected():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            _mock_result([]),
            _mock_result([]),  # never requested a code
        ]

        with pytest.raises(HTTPException) as exc_info:
            email_signup(_signup_req(), _fake_request())
        assert exc_info.value.status_code == 400
        mock_supabase.table.return_value.insert.assert_not_called()


def test_signup_rejects_underage_date_of_birth():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        too_young = (datetime.utcnow() - timedelta(days=365 * 10)).strftime("%Y-%m-%d")

        with pytest.raises(HTTPException) as exc_info:
            email_signup(_signup_req(dob=too_young), _fake_request())
        assert exc_info.value.status_code == 400
        mock_supabase.table.return_value.insert.assert_not_called()


def test_signup_abandoned_otp_never_permanently_blocks_the_email():
    # Someone requests a code and never finishes -- a later, genuine
    # signup attempt for the same email must still be allowed, since
    # no users row was ever created for the abandoned attempt.
    with patch("auth.supabase") as mock_supabase, \
         patch("auth.send_otp_email", return_value=True):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])  # still no users row -- request-otp never creates one
        result = request_email_signup_otp(EmailSignupOtpRequest(email="new@example.com"), _fake_request())
        assert result["success"] is True


# ---- /email/login ----

def test_login_with_correct_credentials_issues_tokens():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "user-1", "password_hash": hash_password("hunters2")}])

        result = email_login(EmailAuthRequest(email="a@example.com", password="hunters2"), _fake_request())

        assert "access_token" in result and "refresh_token" in result
        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert insert_call["user_id"] == "user-1"


def test_login_error_message_does_not_reveal_whether_the_account_exists():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "user-1", "password_hash": hash_password("hunters2")}])
        with pytest.raises(HTTPException) as wrong_password:
            email_login(EmailAuthRequest(email="a@example.com", password="nope"), _fake_request())

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        with pytest.raises(HTTPException) as unknown_email:
            email_login(EmailAuthRequest(email="nobody@example.com", password="anything"), _fake_request())

        assert wrong_password.value.status_code == unknown_email.value.status_code == 401
        assert wrong_password.value.detail == unknown_email.value.detail


# ---- /email/forgot + /email/reset ----

def test_forgot_password_sends_code_for_known_email():
    with patch("auth.supabase") as mock_supabase, \
         patch("auth.send_otp_email", return_value=True) as mock_send:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "user-1"}])

        result = email_forgot_password(EmailForgotRequest(email="a@example.com"), _fake_request())

        assert result["success"] is True
        mock_send.assert_called_once()
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert "email_otp_hash" in update_call and "email_otp_expires_at" in update_call


def test_forgot_password_for_unknown_email_is_404():
    with patch("auth.supabase") as mock_supabase, patch("auth.send_otp_email") as mock_send:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        with pytest.raises(HTTPException) as exc_info:
            email_forgot_password(EmailForgotRequest(email="nobody@example.com"), _fake_request())
        assert exc_info.value.status_code == 404
        mock_send.assert_not_called()


def test_reset_with_correct_code_updates_password_and_kills_sessions():
    future = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = _mock_result([{
            "id": "user-1", "email_otp_hash": _hash_otp("654321"), "email_otp_expires_at": future,
        }])

        result = email_reset_password(
            EmailResetRequest(email="a@example.com", otp="654321", new_password="newpass1"), _fake_request(),
        )

        assert result["success"] is True
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["email_otp_hash"] is None
        # All existing sessions for this user are invalidated.
        mock_supabase.table.return_value.delete.return_value.eq.assert_called_once_with("user_id", "user-1")


def test_reset_with_wrong_code_is_rejected():
    future = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = _mock_result([{
            "id": "user-1", "email_otp_hash": _hash_otp("654321"), "email_otp_expires_at": future,
        }])
        with pytest.raises(HTTPException) as exc_info:
            email_reset_password(
                EmailResetRequest(email="a@example.com", otp="000000", new_password="newpass1"), _fake_request(),
            )
        assert exc_info.value.status_code == 400
        mock_supabase.table.return_value.update.assert_not_called()


def test_reset_with_expired_code_is_rejected():
    past = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = _mock_result([{
            "id": "user-1", "email_otp_hash": _hash_otp("654321"), "email_otp_expires_at": past,
        }])
        with pytest.raises(HTTPException) as exc_info:
            email_reset_password(
                EmailResetRequest(email="a@example.com", otp="654321", new_password="newpass1"), _fake_request(),
            )
        assert exc_info.value.status_code == 400


def test_reset_with_no_pending_reset_is_rejected():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = _mock_result([{
            "id": "user-1", "email_otp_hash": None, "email_otp_expires_at": None,
        }])
        with pytest.raises(HTTPException) as exc_info:
            email_reset_password(
                EmailResetRequest(email="a@example.com", otp="654321", new_password="newpass1"), _fake_request(),
            )
        assert exc_info.value.status_code == 400
