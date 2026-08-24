# ============================================================
# Tests for memory.detect_language — mocking the OpenAI call.
# Patches memory.time.sleep to a no-op so the retry-path tests
# don't actually pause the suite.
# ============================================================

from unittest.mock import patch

from memory import detect_language


def test_language_detected(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create",
               return_value=mock_chat_completion("french")):
        assert detect_language("je suis fatigué") == "french"


def test_empty_response_falls_back_to_english(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create",
               return_value=mock_chat_completion(None)):
        assert detect_language("hello") == "english"


def test_sentence_instead_of_single_word_falls_back_to_english(mock_chat_completion):
    # Sanity guard against the model returning a sentence instead
    # of a single language name.
    content = "I think this is written in French, roughly"
    with patch("memory.openai_client.chat.completions.create",
               return_value=mock_chat_completion(content)):
        assert detect_language("bonjour") == "english"


def test_overlong_response_falls_back_to_english(mock_chat_completion):
    with patch("memory.openai_client.chat.completions.create",
               return_value=mock_chat_completion("a" * 40)):
        assert detect_language("hello") == "english"


def test_empty_input_returns_english_without_calling_api():
    with patch("memory.openai_client.chat.completions.create") as mock_create:
        assert detect_language("") == "english"
        mock_create.assert_not_called()


def test_retries_then_succeeds(mock_chat_completion):
    # Fails once, then succeeds on the second attempt -- should
    # return the real result, not give up after the first failure.
    with patch("memory.time.sleep"), \
         patch("memory.openai_client.chat.completions.create",
               side_effect=[Exception("transient error"), mock_chat_completion("spanish")]) as mock_create:
        result = detect_language("hola", max_retries=2)
    assert result == "spanish"
    assert mock_create.call_count == 2


def test_all_retries_exhausted_falls_back_to_english():
    with patch("memory.openai_client.chat.completions.create", side_effect=Exception("still failing")):
        assert detect_language("hello", max_retries=0) == "english"
