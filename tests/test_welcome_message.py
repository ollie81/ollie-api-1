# ============================================================
# Tests for chat._generate_welcome_message and the /chat/welcome
# route (chat.chat_welcome) -- Ollie's personalized first-ever
# message, right after onboarding. Same style as
# test_mode_starter.py, which this closely mirrors.
# ============================================================

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from chat import chat_welcome, WelcomeRequest


# ---- _generate_welcome_message ----

def test_generation_success_is_used_as_is(mock_chat_completion):
    with patch("chat.openai_client.chat.completions.create",
               return_value=mock_chat_completion("hey Olivia! so excited to meet you 😊 what's on your mind?")), \
         patch("chat.moderate_text", return_value=None):
        import chat
        result = chat._generate_welcome_message("Olivia", {"id": "user-1"}, None)
        assert result == "hey Olivia! so excited to meet you 😊 what's on your mind?"


def test_generation_failure_falls_back_to_a_name_personalized_line():
    with patch("chat.openai_client.chat.completions.create", side_effect=Exception("boom")):
        import chat
        result = chat._generate_welcome_message("Olivia", {"id": "user-1"}, None)
        assert "Olivia" in result


def test_flagged_generation_falls_back(mock_chat_completion):
    with patch("chat.openai_client.chat.completions.create",
               return_value=mock_chat_completion("something flagged")), \
         patch("chat.moderate_text", return_value={"categories": ["x"]}):
        import chat
        result = chat._generate_welcome_message("Olivia", {"id": "user-1"}, None)
        assert "Olivia" in result
        assert result != "something flagged"


def test_empty_generation_falls_back(mock_chat_completion):
    with patch("chat.openai_client.chat.completions.create", return_value=mock_chat_completion(None)), \
         patch("chat.moderate_text", return_value=None):
        import chat
        result = chat._generate_welcome_message("Olivia", {"id": "user-1"}, None)
        assert "Olivia" in result


# ---- chat_welcome route ----

def test_route_saves_greeting_to_history_and_returns_it():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat._generate_welcome_message", return_value="hey Olivia! so excited to meet you"):
        mock_db_cls.return_value.get_or_create_session.return_value = "session-1"

        result = chat_welcome(WelcomeRequest(name="Olivia"), current_user={"id": "user-1"})

        assert result == {"reply": "hey Olivia! so excited to meet you"}
        mock_db_cls.return_value.save_message.assert_called_once_with(
            "user-1", "session-1", "hey Olivia! so excited to meet you", "ollie", 0.0,
        )


def test_route_passes_trimmed_name_to_generation():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat._generate_welcome_message", return_value="hey!") as mock_generate:
        mock_db_cls.return_value.get_or_create_session.return_value = "session-1"

        chat_welcome(WelcomeRequest(name="  Olivia  "), current_user={"id": "user-1"})

        assert mock_generate.call_args[0][0] == "Olivia"


def test_route_defaults_to_a_generic_greeting_word_when_name_is_blank():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat._generate_welcome_message", return_value="hey!") as mock_generate:
        mock_db_cls.return_value.get_or_create_session.return_value = "session-1"

        chat_welcome(WelcomeRequest(name="   "), current_user={"id": "user-1"})

        assert mock_generate.call_args[0][0] == "there"


def test_route_failure_returns_500_not_raw_exception():
    with patch("chat.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.get_or_create_session.side_effect = Exception("db down")
        with pytest.raises(HTTPException) as exc_info:
            chat_welcome(WelcomeRequest(name="Olivia"), current_user={"id": "user-1"})
        assert exc_info.value.status_code == 500
