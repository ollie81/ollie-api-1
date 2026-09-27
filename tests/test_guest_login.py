# ============================================================
# Tests for auth.guest_login (POST /auth/guest) -- first contact for
# guest mode. The client generates its own id (a UUID persisted in
# localStorage) and this either creates a lightweight guest row for
# it or, if that id already has one, just issues a fresh token pair
# for the same guest.
# ============================================================

from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from auth import guest_login, GuestRequest


def _fake_request():
    return Request(scope={
        "type": "http", "method": "POST", "path": "/",
        "headers": [], "client": ("testclient", 123), "query_string": b"",
    })


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


def test_new_guest_id_creates_a_guest_row():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        mock_supabase.table.return_value.insert.return_value.execute.return_value = _mock_result([])

        result = guest_login(GuestRequest(guest_id="guest-1"), _fake_request())

        assert result["success"] is True
        assert "access_token" in result and "refresh_token" in result
        insert_calls = mock_supabase.table.return_value.insert.call_args_list
        users_insert = next(c[0][0] for c in insert_calls if "is_guest" in c[0][0])
        assert users_insert["id"] == "guest-1"
        assert users_insert["is_guest"] is True
        assert users_insert["phone"] == "guest:guest-1"


def test_existing_guest_id_reuses_the_row_without_inserting_a_new_one():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "guest-1", "is_guest": True}])

        result = guest_login(GuestRequest(guest_id="guest-1"), _fake_request())

        assert "access_token" in result
        insert_calls = mock_supabase.table.return_value.insert.call_args_list
        assert not any("is_guest" in c[0][0] for c in insert_calls)


def test_guest_id_belonging_to_a_real_account_is_rejected():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "user-1", "is_guest": False}])

        with pytest.raises(HTTPException) as exc_info:
            guest_login(GuestRequest(guest_id="user-1"), _fake_request())

        assert exc_info.value.status_code == 400
        mock_supabase.table.return_value.insert.assert_not_called()


def test_db_failure_returns_clean_500_not_raw_exception():
    with patch("auth.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.side_effect = Exception("db down")

        with pytest.raises(HTTPException) as exc_info:
            guest_login(GuestRequest(guest_id="guest-1"), _fake_request())

        assert exc_info.value.status_code == 500
