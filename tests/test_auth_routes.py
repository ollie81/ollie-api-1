# ============================================================
# Tests for the auth.py route-level flows -- get_current_user
# (the dependency gating every protected endpoint in the app),
# /login, /refresh (single-use token rotation), and /logout.
#
# Phase 9 regression pass: these were previously only covered at
# the helper-function level (hash_password/_check_age_gate), never
# at the route level -- this fills that gap, focused on the
# highest-blast-radius pieces: can a token for one user ever
# resolve to another user's data, and does /refresh's single-use
# rotation actually behave as single-use.
#
# Routes are called directly (same style as the rest of this
# suite), which means a real (if minimal) starlette Request is
# needed for the @limiter.limit-decorated routes -- slowapi
# rejects a MagicMock with "must be an instance of
# starlette.requests.Request".
# ============================================================

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import auth
from auth import (
    get_current_user,
    login,
    refresh_token,
    logout,
    AuthRequest,
    RefreshRequest,
    LogoutRequest,
    create_access_token,
    hash_password,
)
from config import JWT_SECRET, JWT_ALGORITHM


def _fake_request(path="/"):
    return Request(scope={
        "type": "http", "method": "POST", "path": path,
        "headers": [], "client": ("testclient", 123), "query_string": b"",
    })


def _bearer(token):
    return MagicMock(credentials=token)


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


# ---- get_current_user ----

def test_valid_token_resolves_to_the_matching_user_row():
    token = create_access_token("user-1")
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "user-1", "username": "alice"}])
        user = get_current_user(_bearer(token))

        assert user == {"id": "user-1", "username": "alice"}
        # Scoped by the id embedded in the token, not any client input.
        mock_supabase.table.return_value.select.return_value.eq.assert_called_once_with("id", "user-1")


def test_token_for_a_deleted_user_is_rejected():
    token = create_access_token("user-deleted")
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(_bearer(token))
        assert exc_info.value.status_code == 401


def test_expired_token_is_rejected():
    expired = jwt.encode(
        {"sub": "user-1", "type": "access", "exp": datetime.utcnow() - timedelta(minutes=1)},
        JWT_SECRET, algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_bearer(expired))
    assert exc_info.value.status_code == 401


def test_garbage_token_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_bearer("not-a-real-jwt"))
    assert exc_info.value.status_code == 401


def test_refresh_shaped_token_is_not_accepted_as_access_token():
    # Only create_access_token ever sets type="access" -- this is
    # the backstop that keeps a differently-typed token from being
    # accepted here even if something else in the codebase ever
    # starts minting JWTs for another purpose.
    wrong_type = jwt.encode(
        {"sub": "user-1", "type": "refresh", "exp": datetime.utcnow() + timedelta(minutes=5)},
        JWT_SECRET, algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_bearer(wrong_type))
    assert exc_info.value.status_code == 401


# ---- /login ----

def test_login_with_correct_credentials_issues_tokens_scoped_to_that_user():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "user-1", "password_hash": hash_password("correct horse")}])

        result = login(AuthRequest(phone_number="+15551234567", password="correct horse"), _fake_request())

        assert result["success"] is True
        assert "access_token" in result and "refresh_token" in result
        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert insert_call["user_id"] == "user-1"


def test_login_with_wrong_password_is_rejected():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "user-1", "password_hash": hash_password("correct horse")}])

        with pytest.raises(HTTPException) as exc_info:
            login(AuthRequest(phone_number="+15551234567", password="wrong guess"), _fake_request())
        assert exc_info.value.status_code == 401


def test_login_with_unknown_phone_number_is_rejected():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])

        with pytest.raises(HTTPException) as exc_info:
            login(AuthRequest(phone_number="+15559999999", password="anything"), _fake_request())
        assert exc_info.value.status_code == 401


def test_login_error_message_does_not_reveal_whether_the_account_exists():
    # Wrong password and unknown phone must be indistinguishable to
    # the caller -- otherwise /login becomes a phone-number oracle.
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "user-1", "password_hash": hash_password("correct horse")}])
        with pytest.raises(HTTPException) as wrong_password_exc:
            login(AuthRequest(phone_number="+15551234567", password="wrong guess"), _fake_request())

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        with pytest.raises(HTTPException) as unknown_phone_exc:
            login(AuthRequest(phone_number="+15559999999", password="anything"), _fake_request())

        assert wrong_password_exc.value.status_code == unknown_phone_exc.value.status_code
        assert wrong_password_exc.value.detail == unknown_phone_exc.value.detail


# ---- /refresh: single-use token rotation ----

def test_refresh_with_a_valid_token_rotates_it_and_keeps_the_same_user():
    future_expiry = (datetime.utcnow() + timedelta(days=10)).isoformat()
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"user_id": "user-1", "expires_at": future_expiry, "token_hash": "old-hash"}])

        result = refresh_token(RefreshRequest(refresh_token="some-refresh-token"))

        assert "access_token" in result and "refresh_token" in result
        # Old token deleted (single-use)...
        mock_supabase.table.return_value.delete.return_value.eq.assert_called_once()
        # ...and the new row is issued for the SAME user the old
        # token belonged to, never anything client-supplied.
        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert insert_call["user_id"] == "user-1"

        # The new access token really does carry that user's id.
        decoded = jwt.decode(result["access_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert decoded["sub"] == "user-1"


def test_refresh_with_an_unknown_token_is_rejected():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        with pytest.raises(HTTPException) as exc_info:
            refresh_token(RefreshRequest(refresh_token="never-issued"))
        assert exc_info.value.status_code == 401


def test_refresh_with_an_expired_token_is_rejected_and_cleaned_up():
    past_expiry = (datetime.utcnow() - timedelta(days=1)).isoformat()
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"user_id": "user-1", "expires_at": past_expiry, "token_hash": "old-hash"}])

        with pytest.raises(HTTPException) as exc_info:
            refresh_token(RefreshRequest(refresh_token="stale-token"))
        assert exc_info.value.status_code == 401
        mock_supabase.table.return_value.delete.return_value.eq.assert_called_once()


def test_a_reused_refresh_token_is_rejected_the_second_time():
    # Simulates the real single-use flow: first call's row exists,
    # second call (replaying the same now-deleted token) finds nothing.
    future_expiry = (datetime.utcnow() + timedelta(days=10)).isoformat()
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            _mock_result([{"user_id": "user-1", "expires_at": future_expiry, "token_hash": "old-hash"}]),
            _mock_result([]),
        ]
        refresh_token(RefreshRequest(refresh_token="one-time-token"))
        with pytest.raises(HTTPException) as exc_info:
            refresh_token(RefreshRequest(refresh_token="one-time-token"))
        assert exc_info.value.status_code == 401


# ---- /logout ----

def test_logout_deletes_the_matching_refresh_token_row():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.delete.return_value.eq.return_value.execute.return_value = MagicMock()
        result = logout(LogoutRequest(refresh_token="some-token"))
        assert result == {"success": True, "message": "Logged out"}
        mock_supabase.table.return_value.delete.return_value.eq.assert_called_once()
        eq_args = mock_supabase.table.return_value.delete.return_value.eq.call_args[0]
        assert eq_args[0] == "token_hash"
        assert eq_args[1] == auth.hash_refresh_token("some-token")
