# ============================================================
# Tests for memory.detect_goal_completion — mocking the OpenAI
# call. The connective tissue behind "you finally got that login
# working": checks whether a message indicates one of the user's
# EXISTING active goals was just finished.
# ============================================================

import json
from unittest.mock import patch

from memory import detect_goal_completion


def test_completion_detected_returns_matching_title(mock_chat_completion):
    content = json.dumps({"completed": True, "goal": "fix the login bug"})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        result = detect_goal_completion(["fix the login bug", "run a marathon"], "I finally got the login working!")
    assert result == "fix the login bug"


def test_not_completed_returns_none(mock_chat_completion):
    content = json.dumps({"completed": False, "goal": ""})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert detect_goal_completion(["fix the login bug"], "still stuck on the login bug") is None


def test_goal_not_in_active_list_is_rejected(mock_chat_completion):
    # Model hallucinated a title that isn't actually one of the
    # active goals passed in -- must not be trusted.
    content = json.dumps({"completed": True, "goal": "something made up"})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert detect_goal_completion(["fix the login bug"], "done!") is None


def test_no_active_goals_short_circuits_without_calling_api():
    with patch("memory.openai_client.chat.completions.create") as mock_create:
        assert detect_goal_completion([], "I finished it!") is None
        mock_create.assert_not_called()


def test_empty_input_short_circuits_without_calling_api():
    with patch("memory.openai_client.chat.completions.create") as mock_create:
        assert detect_goal_completion(["fix the login bug"], "") is None
        mock_create.assert_not_called()


def test_malformed_json_returns_none_not_raises(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion("nonsense")):
        assert detect_goal_completion(["fix the login bug"], "done!") is None


def test_empty_response_content_returns_none(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(None)):
        assert detect_goal_completion(["fix the login bug"], "done!") is None


def test_api_exception_returns_none_not_raises():
    with patch("memory.openai_client.chat.completions.create", side_effect=Exception("timeout")):
        assert detect_goal_completion(["fix the login bug"], "done!") is None
