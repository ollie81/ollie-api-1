# ============================================================
# Tests for chat._generate_mode_opener and the /chat/mode-starter
# route (chat.chat_mode_starter) -- "Do It With Me" Ollie-speaks-
# first opener. Route is called directly (same style as
# test_settings_usage.py), dependencies mocked out.
# ============================================================

from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from chat import chat_mode_starter, ModeStarterRequest
from modes import MODE_OPENER_FALLBACKS


def _fake_request(path="/"):
    return Request(scope={
        "type": "http", "method": "POST", "path": path,
        "headers": [], "client": ("testclient", 123), "query_string": b"",
    })


# ---- _generate_mode_opener ----

def test_generation_success_is_used_as_is(mock_chat_completion):
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client.chat.completions.create",
               return_value=mock_chat_completion("hey! what are we studying today?")), \
         patch("chat.moderate_text", return_value=None):
        mock_db_cls.return_value.get_relevant_memories.return_value = []
        mock_db_cls.return_value.get_user_context.return_value = {}

        import chat
        result = chat._generate_mode_opener("user-1", "study", {"id": "user-1"}, None)
        assert result == "hey! what are we studying today?"


def test_generation_failure_falls_back_to_mode_specific_line():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False):
        mock_db_cls.return_value.get_relevant_memories.side_effect = Exception("boom")

        import chat
        result = chat._generate_mode_opener("user-1", "plan_day", {"id": "user-1"}, None)
        assert result == MODE_OPENER_FALLBACKS["plan_day"]


def test_flagged_generation_falls_back(mock_chat_completion):
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client.chat.completions.create",
               return_value=mock_chat_completion("something flagged")), \
         patch("chat.moderate_text", return_value={"categories": ["x"]}):
        mock_db_cls.return_value.get_relevant_memories.return_value = []
        mock_db_cls.return_value.get_user_context.return_value = {}

        import chat
        result = chat._generate_mode_opener("user-1", "brainstorm", {"id": "user-1"}, None)
        assert result == MODE_OPENER_FALLBACKS["brainstorm"]


def test_memory_disabled_skips_retrieval(mock_chat_completion):
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.openai_client.chat.completions.create",
               return_value=mock_chat_completion("let's build something!")), \
         patch("chat.moderate_text", return_value=None):
        import chat
        chat._generate_mode_opener("user-1", "build", {"id": "user-1", "memory_enabled": False}, None)
        mock_db_cls.return_value.get_relevant_memories.assert_not_called()


# ---- chat_mode_starter route ----

def test_route_saves_opener_to_history_and_returns_it():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat._generate_mode_opener", return_value="hey! what are we studying today?"):
        mock_db_cls.return_value.get_or_create_session.return_value = "session-1"

        result = chat_mode_starter(ModeStarterRequest(mode="study"), _fake_request(), current_user={"id": "user-1"})

        assert result == {"reply": "hey! what are we studying today?", "mode": "study"}
        mock_db_cls.return_value.save_message.assert_called_once_with(
            "user-1", "session-1", "hey! what are we studying today?", "ollie", 0.0,
        )


def test_route_rejects_unknown_mode():
    with pytest.raises(HTTPException) as exc_info:
        chat_mode_starter(ModeStarterRequest(mode="not_a_real_mode"), _fake_request(), current_user={"id": "user-1"})
    assert exc_info.value.status_code == 400


def test_route_does_not_touch_streak_or_message_count():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat._generate_mode_opener", return_value="hey!"):
        mock_db_cls.return_value.get_or_create_session.return_value = "session-1"

        chat_mode_starter(ModeStarterRequest(mode="learn"), _fake_request(), current_user={"id": "user-1"})

        mock_db_cls.return_value.update_streak.assert_not_called()
        mock_db_cls.return_value.increment_message_count.assert_not_called()
        mock_db_cls.return_value.try_consume_message.assert_not_called()


def test_route_failure_returns_500_not_raw_exception():
    with patch("chat.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.get_or_create_session.side_effect = Exception("db down")
        with pytest.raises(HTTPException) as exc_info:
            chat_mode_starter(ModeStarterRequest(mode="practice"), _fake_request(), current_user={"id": "user-1"})
        assert exc_info.value.status_code == 500
