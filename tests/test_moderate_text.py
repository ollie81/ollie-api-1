# ============================================================
# Tests for memory.moderate_text — mocking the OpenAI moderation
# call. This one is deliberately observability-only (see the
# comment above it in memory.py) -- these tests check the
# extraction/formatting logic, not any blocking behavior, since
# it never blocks anything by design.
# ============================================================

from unittest.mock import patch

from memory import moderate_text


def test_flagged_content_returns_categories(mock_moderation_response):
    categories = {"harassment": True, "violence": False, "sexual": False}
    with patch("memory.openai_client.moderations.create",
               return_value=mock_moderation_response(True, categories)):
        result = moderate_text("some harassing message")
    assert result == {"flagged": True, "categories": ["harassment"]}


def test_multiple_flagged_categories(mock_moderation_response):
    categories = {"harassment": True, "violence": True, "sexual": False}
    with patch("memory.openai_client.moderations.create",
               return_value=mock_moderation_response(True, categories)):
        result = moderate_text("something bad")
    assert result["flagged"] is True
    assert set(result["categories"]) == {"harassment", "violence"}


def test_not_flagged_returns_none(mock_moderation_response):
    categories = {"harassment": False, "violence": False, "sexual": False}
    with patch("memory.openai_client.moderations.create",
               return_value=mock_moderation_response(False, categories)):
        assert moderate_text("hey what's up") is None


def test_api_exception_returns_none_not_raises():
    with patch("memory.openai_client.moderations.create", side_effect=Exception("service down")):
        assert moderate_text("some message") is None


def test_empty_input_short_circuits_without_calling_api():
    with patch("memory.openai_client.moderations.create") as mock_create:
        assert moderate_text("") is None
        mock_create.assert_not_called()
