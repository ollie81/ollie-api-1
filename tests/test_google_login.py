# ============================================================
# Tests for auth.google_login (POST /auth/google) -- previously
# entirely untested (only ever mentioned in a comment elsewhere).
# Focus: is_new_user, added to let the client show onboarding only
# on a genuinely first-ever Google sign-in, not every login.
# ============================================================

from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from auth import google_login, GoogleAuthRequest


def _fake_request():
    return Request(scope={
        "type": "http", "method": "POST", "path": "/",
        "headers": [], "client": ("testclient", 123), "query_string": b"",
    })


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


def _mock_token_info(email="new@example.com", name="Olivia"):
    return {"email": email, "name": name}


def test_new_google_user_creates_a_row_and_reports_is_new_user_true():
    with patch("auth.id_token.verify_oauth2_token", return_value=_mock_token_info()), \
         patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        mock_supabase.table.return_value.insert.return_value.execute.return_value = \
            _mock_result([{"id": "user-1", "username": "Olivia"}])

        result = google_login(GoogleAuthRequest(id_token="fake-token"), _fake_request())

        assert result["is_new_user"] is True
        assert result["username"] == "Olivia"
        assert "access_token" in result and "refresh_token" in result
        insert_call = mock_supabase.table.return_value.insert.call_args_list[0][0][0]
        assert insert_call["phone"] == "new@example.com"
        assert insert_call["username"] == "Olivia"


def test_returning_google_user_does_not_insert_and_reports_is_new_user_false():
    with patch("auth.id_token.verify_oauth2_token", return_value=_mock_token_info(email="returning@example.com")), \
         patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "user-1", "username": "Olivia"}])

        result = google_login(GoogleAuthRequest(id_token="fake-token"), _fake_request())

        assert result["is_new_user"] is False
        assert result["username"] == "Olivia"
        # Only the refresh_tokens insert happens on a returning login
        # -- no new users row. The mock doesn't distinguish table
        # names, so check the one insert that did happen isn't a
        # users-row shape (no phone/username keys) rather than
        # asserting insert was never called at all.
        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert "phone" not in insert_call and "username" not in insert_call


def test_invalid_token_is_rejected():
    with patch("auth.id_token.verify_oauth2_token", side_effect=ValueError("invalid token")):
        with pytest.raises(HTTPException) as exc_info:
            google_login(GoogleAuthRequest(id_token="garbage"), _fake_request())
        assert exc_info.value.status_code == 401
