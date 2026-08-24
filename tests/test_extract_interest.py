# ============================================================
# Tests for interest_memory.extract_interest — mocking the
# OpenAI call.
# ============================================================

import json
from unittest.mock import patch

from interest_memory import extract_interest


def test_interest_detected(mock_chat_completion):
    content = json.dumps({"has_interest": True, "interest": "anime"})
    with patch("interest_memory.openai_client.chat.completions.create",
               return_value=mock_chat_completion(content)):
        assert extract_interest("i've been watching so much anime lately") == "anime"


def test_lowercased(mock_chat_completion):
    content = json.dumps({"has_interest": True, "interest": "Arsenal FC"})
    with patch("interest_memory.openai_client.chat.completions.create",
               return_value=mock_chat_completion(content)):
        assert extract_interest("arsenal lost again") == "arsenal fc"


def test_no_interest_expressed_returns_none(mock_chat_completion):
    content = json.dumps({"has_interest": False, "interest": ""})
    with patch("interest_memory.openai_client.chat.completions.create",
               return_value=mock_chat_completion(content)):
        assert extract_interest("what time works for you tomorrow") is None


def test_overlong_interest_rejected(mock_chat_completion):
    content = json.dumps({"has_interest": True, "interest": "a" * 60})
    with patch("interest_memory.openai_client.chat.completions.create",
               return_value=mock_chat_completion(content)):
        assert extract_interest("...") is None


def test_malformed_json_returns_none_not_raises(mock_chat_completion):
    with patch("interest_memory.openai_client.chat.completions.create",
               return_value=mock_chat_completion("nonsense")):
        assert extract_interest("i love gaming") is None


def test_empty_response_content_returns_none(mock_chat_completion):
    with patch("interest_memory.openai_client.chat.completions.create",
               return_value=mock_chat_completion(None)):
        assert extract_interest("i love gaming") is None


def test_api_exception_returns_none_not_raises():
    with patch("interest_memory.openai_client.chat.completions.create", side_effect=Exception("timeout")):
        assert extract_interest("i love gaming") is None


def test_empty_input_short_circuits_without_calling_api():
    with patch("interest_memory.openai_client.chat.completions.create") as mock_create:
        assert extract_interest("") is None
        mock_create.assert_not_called()
