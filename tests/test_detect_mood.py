# ============================================================
# Tests for memory.detect_mood — mocking the OpenAI call.
# ============================================================

import json
from unittest.mock import patch

from memory import detect_mood


def test_mood_detected(mock_chat_completion):
    content = json.dumps({"has_mood": True, "mood": "stressed"})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert detect_mood("work has been so stressful lately") == "stressed"


def test_no_mood_conveyed_returns_none(mock_chat_completion):
    content = json.dumps({"has_mood": False, "mood": ""})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert detect_mood("what time works for you tomorrow") is None


def test_multi_word_mood_rejected(mock_chat_completion):
    # Only a single lowercase word is a valid mood label.
    content = json.dumps({"has_mood": True, "mood": "kind of anxious"})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert detect_mood("i guess i'm kind of anxious") is None


def test_overlong_mood_rejected(mock_chat_completion):
    content = json.dumps({"has_mood": True, "mood": "a" * 25})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert detect_mood("...") is None


def test_malformed_json_returns_none_not_raises(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion("nonsense")):
        assert detect_mood("i feel great") is None


def test_empty_response_content_returns_none(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(None)):
        assert detect_mood("i feel great") is None


def test_api_exception_returns_none_not_raises():
    with patch("memory.openai_client.chat.completions.create", side_effect=Exception("rate limited")):
        assert detect_mood("i feel great") is None


def test_empty_input_short_circuits_without_calling_api():
    with patch("memory.openai_client.chat.completions.create") as mock_create:
        assert detect_mood("") is None
        mock_create.assert_not_called()
