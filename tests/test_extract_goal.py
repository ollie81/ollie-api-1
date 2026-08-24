# ============================================================
# Tests for memory.extract_goal — mocking the OpenAI call.
# ============================================================

import json
from unittest.mock import patch

from memory import extract_goal


def test_goal_detected(mock_chat_completion):
    content = json.dumps({"has_goal": True, "goal": "run a marathon this year"})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert extract_goal("my goal is to run a marathon this year") == "run a marathon this year"


def test_no_goal_expressed_returns_none(mock_chat_completion):
    content = json.dumps({"has_goal": False, "goal": ""})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert extract_goal("what time works for you tomorrow") is None


def test_overlong_goal_rejected(mock_chat_completion):
    content = json.dumps({"has_goal": True, "goal": "a" * 150})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert extract_goal("...") is None


def test_malformed_json_returns_none_not_raises(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion("nonsense")):
        assert extract_goal("i want to save money") is None


def test_empty_response_content_returns_none(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(None)):
        assert extract_goal("i want to save money") is None


def test_api_exception_returns_none_not_raises():
    with patch("memory.openai_client.chat.completions.create", side_effect=Exception("timeout")):
        assert extract_goal("i want to save money") is None


def test_empty_input_short_circuits_without_calling_api():
    with patch("memory.openai_client.chat.completions.create") as mock_create:
        assert extract_goal("") is None
        mock_create.assert_not_called()
