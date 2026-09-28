# ============================================================
# Tests for the /chat route's guest-mode gating (see auth.py's
# /auth/guest) -- a guest has its own much smaller, one-time cap,
# checked before (and instead of) the normal premium/free-tier
# daily-cap branch, with a distinct 429 detail so the client can
# tell "guest limit -- go sign up" apart from "daily limit -- come
# back tomorrow".
# ============================================================

from unittest.mock import patch, MagicMock

from fastapi import BackgroundTasks, HTTPException
import pytest
from starlette.requests import Request

from chat import chat, ChatRequest, GUEST_MESSAGE_LIMIT_DETAIL


def _fake_request(path="/"):
    return Request(scope={
        "type": "http", "method": "POST", "path": path,
        "headers": [], "client": ("testclient", 123), "query_string": b"",
    })


def _run(current_user, mock_db_cls, mock_is_premium):
    req = ChatRequest(message="hey")
    return chat(req, _fake_request(), BackgroundTasks(), current_user=current_user)


def test_guest_under_cap_consumes_guest_message_not_free_tier():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat._process_chat_message", return_value={"reply": "hey!"}):
        db = mock_db_cls.return_value
        db.try_consume_guest_message.return_value = 6

        result = _run({"id": "guest-1", "is_guest": True}, mock_db_cls, None)

        db.try_consume_guest_message.assert_called_once_with("guest-1")
        db.try_consume_message.assert_not_called()
        db.increment_message_count.assert_not_called()
        assert result["guest_messages_remaining"] == 6
        assert result["reply"] == "hey!"


def test_guest_last_message_reports_zero_remaining_not_omitted():
    # 0 is a real, successful remaining count, not the None/failure
    # sentinel -- must still show up in the response.
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat._process_chat_message", return_value={"reply": "hey!"}):
        db = mock_db_cls.return_value
        db.try_consume_guest_message.return_value = 0

        result = _run({"id": "guest-1", "is_guest": True}, mock_db_cls, None)

        assert "guest_messages_remaining" in result
        assert result["guest_messages_remaining"] == 0


def test_guest_at_cap_gets_distinct_429_detail():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat._process_chat_message", return_value={"reply": "hey!"}) as mock_process:
        db = mock_db_cls.return_value
        db.try_consume_guest_message.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            _run({"id": "guest-1", "is_guest": True}, mock_db_cls, None)

        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == GUEST_MESSAGE_LIMIT_DETAIL
        assert exc_info.value.detail != "Daily limit reached"
        mock_process.assert_not_called()


def test_real_free_tier_user_unaffected_by_guest_gate():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat._process_chat_message", return_value={"reply": "hey!"}):
        db = mock_db_cls.return_value
        db.try_consume_message.return_value = True

        result = _run({"id": "user-1", "is_guest": False}, mock_db_cls, None)

        db.try_consume_message.assert_called_once_with("user-1")
        db.try_consume_guest_message.assert_not_called()
        assert "guest_messages_remaining" not in result


def test_missing_is_guest_key_treated_as_a_real_user():
    # current_user is a plain dict from the DB row -- is_guest may be
    # absent entirely for accounts that existed before this migration.
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=True), \
         patch("chat._process_chat_message", return_value={"reply": "hey!"}):
        db = mock_db_cls.return_value

        _run({"id": "user-1"}, mock_db_cls, None)

        db.increment_message_count.assert_called_once_with("user-1")
        db.try_consume_guest_message.assert_not_called()
