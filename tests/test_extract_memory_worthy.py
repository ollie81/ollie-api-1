# ============================================================
# Tests for memory.extract_memory_worthy — mocking the OpenAI
# call. Replaces the old keyword-trigger version's tests; see
# memory.py for why (judgment-based, not phrase-matching).
# ============================================================

import json
from unittest.mock import patch

from memory import extract_memory_worthy


def test_worthy_memory_returns_text_category_and_importance(mock_chat_completion):
    content = json.dumps({
        "worth_remembering": True,
        "memory": "Has a dog named Max",
        "category": "person",
        "importance": 2,
    })
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        text, category, importance = extract_memory_worthy("i have a dog named Max")
    assert text == "Has a dog named Max"
    assert category == "person"
    assert importance == 2


def test_not_worth_remembering_returns_none_triple(mock_chat_completion):
    content = json.dumps({"worth_remembering": False, "memory": "", "category": "", "importance": 1})
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert extract_memory_worthy("what's up") == (None, None, 0)


def test_unknown_category_is_normalized_to_none(mock_chat_completion):
    content = json.dumps({
        "worth_remembering": True,
        "memory": "Something notable",
        "category": "not_a_real_category",
        "importance": 1,
    })
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        text, category, importance = extract_memory_worthy("something notable happened")
    assert text == "Something notable"
    assert category is None
    assert importance == 1


def test_invalid_importance_defaults_to_one(mock_chat_completion):
    content = json.dumps({
        "worth_remembering": True,
        "memory": "Something notable",
        "category": "event",
        "importance": 99,
    })
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        _, _, importance = extract_memory_worthy("something notable happened")
    assert importance == 1


def test_overlong_memory_rejected(mock_chat_completion):
    content = json.dumps({
        "worth_remembering": True,
        "memory": "a" * 250,
        "category": "event",
        "importance": 1,
    })
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(content)):
        assert extract_memory_worthy("...") == (None, None, 0)


def test_malformed_json_returns_none_triple_not_raises(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion("nonsense")):
        assert extract_memory_worthy("i finally got the login bug fixed") == (None, None, 0)


def test_empty_response_content_returns_none_triple(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create", return_value=mock_chat_completion(None)):
        assert extract_memory_worthy("something happened") == (None, None, 0)


def test_api_exception_returns_none_triple_not_raises():
    with patch("memory.openai_client.chat.completions.create", side_effect=Exception("timeout")):
        assert extract_memory_worthy("something happened") == (None, None, 0)


def test_empty_input_short_circuits_without_calling_api():
    with patch("memory.openai_client.chat.completions.create") as mock_create:
        assert extract_memory_worthy("") == (None, None, 0)
        assert extract_memory_worthy(None) == (None, None, 0)
        mock_create.assert_not_called()


def test_too_short_input_short_circuits_without_calling_api():
    with patch("memory.openai_client.chat.completions.create") as mock_create:
        assert extract_memory_worthy("hi") == (None, None, 0)
        mock_create.assert_not_called()
