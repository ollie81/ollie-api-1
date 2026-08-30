# ============================================================
# Tests for GET /history (chat.get_history) and GET /chat/search
# (chat.search_chat) -- both route functions called directly, same
# style as test_premium_status.py. Neither had dedicated route-level
# coverage before this.
# ============================================================

from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

from chat import get_history, search_chat


def test_history_includes_id_and_no_reply_to_when_absent():
    with patch("chat.OllieDB") as mock_db_cls:
        db = mock_db_cls.return_value
        db.get_conversation_history.return_value = [
            {"id": "m1", "sender": "user", "message": "hi", "created_at": "t1", "reply_to_id": None},
        ]
        db.get_messages_by_ids.return_value = {}

        result = get_history(current_user={"id": "user-1"})

        assert result["messages"] == [
            {"id": "m1", "sender": "user", "message": "hi", "created_at": "t1"},
        ]
        db.get_messages_by_ids.assert_called_once_with([])


def test_history_resolves_reply_to_preview():
    with patch("chat.OllieDB") as mock_db_cls:
        db = mock_db_cls.return_value
        db.get_conversation_history.return_value = [
            {"id": "m1", "sender": "ollie", "message": "how'd the exam go?", "created_at": "t1", "reply_to_id": None},
            {"id": "m2", "sender": "user", "message": "it went great!", "created_at": "t2", "reply_to_id": "m1"},
        ]
        db.get_messages_by_ids.return_value = {
            "m1": {"id": "m1", "sender": "ollie", "message": "how'd the exam go?"},
        }

        result = get_history(current_user={"id": "user-1"})

        assert "reply_to" not in result["messages"][0]
        assert result["messages"][1]["reply_to"] == {"sender": "ollie", "message": "how'd the exam go?"}
        db.get_messages_by_ids.assert_called_once_with(["m1"])


def test_search_chat_returns_results():
    with patch("chat.OllieDB") as mock_db_cls:
        db = mock_db_cls.return_value
        db.search_messages.return_value = [
            {"id": "m1", "sender": "ollie", "message": "let's talk about your exam", "created_at": "t1"},
        ]

        result = search_chat(q="exam", current_user={"id": "user-1"})

        assert result == {"results": [
            {"id": "m1", "sender": "ollie", "message": "let's talk about your exam", "created_at": "t1"},
        ]}
        db.search_messages.assert_called_once_with("user-1", "exam", limit=50)


def test_search_chat_rejects_blank_query():
    with patch("chat.OllieDB"):
        with pytest.raises(HTTPException) as exc_info:
            search_chat(q="   ", current_user={"id": "user-1"})

        assert exc_info.value.status_code == 400


def test_search_chat_strips_whitespace():
    with patch("chat.OllieDB") as mock_db_cls:
        db = mock_db_cls.return_value
        db.search_messages.return_value = []

        search_chat(q="  exam  ", current_user={"id": "user-1"})

        db.search_messages.assert_called_once_with("user-1", "exam", limit=50)
