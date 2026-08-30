# ============================================================
# Tests for email_service.send_otp_email / _parse_from_address --
# the SendGrid integration backing /auth/email/* (see auth.py).
# requests.post is mocked out; nothing here makes a real network
# call. Same style as test_speak_route.py's mocking of an external
# HTTP-based provider.
# ============================================================

from unittest.mock import patch, MagicMock

import email_service
from email_service import send_otp_email, _parse_from_address


# ---- _parse_from_address ----

def test_parses_name_and_email_format():
    assert _parse_from_address("Ollie <ollie@example.com>") == {
        "email": "ollie@example.com", "name": "Ollie",
    }


def test_parses_plain_email_with_no_name():
    assert _parse_from_address("ollie@example.com") == {"email": "ollie@example.com"}


def test_strips_whitespace_around_name_and_email():
    assert _parse_from_address("  Ollie   <  ollie@example.com  >  ") == {
        "email": "ollie@example.com", "name": "Ollie",
    }


# ---- send_otp_email ----

def _mock_response(status_code=202, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    return response


def test_missing_api_key_skips_send_and_returns_false():
    with patch.object(email_service, "SENDGRID_API_KEY", None), \
         patch.object(email_service, "SENDGRID_FROM_EMAIL", "ollie@example.com"), \
         patch("email_service.requests.post") as mock_post:
        assert send_otp_email("user@example.com", "123456") is False
        mock_post.assert_not_called()


def test_missing_from_email_skips_send_and_returns_false():
    with patch.object(email_service, "SENDGRID_API_KEY", "key"), \
         patch.object(email_service, "SENDGRID_FROM_EMAIL", None), \
         patch("email_service.requests.post") as mock_post:
        assert send_otp_email("user@example.com", "123456") is False
        mock_post.assert_not_called()


def test_successful_send_returns_true_with_correct_payload_shape():
    with patch.object(email_service, "SENDGRID_API_KEY", "key-123"), \
         patch.object(email_service, "SENDGRID_FROM_EMAIL", "Ollie <ollie@example.com>"), \
         patch("email_service.requests.post", return_value=_mock_response(202)) as mock_post:
        result = send_otp_email("user@example.com", "654321", purpose="verify")

        assert result is True
        call = mock_post.call_args
        assert call[0][0] == "https://api.sendgrid.com/v3/mail/send"
        assert call[1]["headers"]["Authorization"] == "Bearer key-123"
        body = call[1]["json"]
        assert body["personalizations"] == [{"to": [{"email": "user@example.com"}]}]
        assert body["from"] == {"email": "ollie@example.com", "name": "Ollie"}
        assert "654321" in body["content"][0]["value"]


def test_verify_purpose_uses_verification_copy():
    with patch.object(email_service, "SENDGRID_API_KEY", "key"), \
         patch.object(email_service, "SENDGRID_FROM_EMAIL", "ollie@example.com"), \
         patch("email_service.requests.post", return_value=_mock_response(202)) as mock_post:
        send_otp_email("user@example.com", "111111", purpose="verify")
        body = mock_post.call_args[1]["json"]
        assert body["subject"] == "Verify your email for Ollie"


def test_reset_purpose_uses_reset_copy():
    with patch.object(email_service, "SENDGRID_API_KEY", "key"), \
         patch.object(email_service, "SENDGRID_FROM_EMAIL", "ollie@example.com"), \
         patch("email_service.requests.post", return_value=_mock_response(202)) as mock_post:
        send_otp_email("user@example.com", "111111", purpose="reset")
        body = mock_post.call_args[1]["json"]
        assert body["subject"] == "Reset your Ollie password"


def test_sendgrid_error_status_returns_false():
    with patch.object(email_service, "SENDGRID_API_KEY", "key"), \
         patch.object(email_service, "SENDGRID_FROM_EMAIL", "ollie@example.com"), \
         patch("email_service.requests.post", return_value=_mock_response(401, "Unauthorized")):
        assert send_otp_email("user@example.com", "111111") is False


def test_network_failure_returns_false_not_raises():
    with patch.object(email_service, "SENDGRID_API_KEY", "key"), \
         patch.object(email_service, "SENDGRID_FROM_EMAIL", "ollie@example.com"), \
         patch("email_service.requests.post", side_effect=Exception("connection reset")):
        assert send_otp_email("user@example.com", "111111") is False
