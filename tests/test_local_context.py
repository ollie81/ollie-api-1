from unittest.mock import MagicMock, patch

from local_context import get_local_highlight


def test_no_location_short_circuits_without_any_lookup():
    with patch("local_context.get_top_interests") as mock_interests, \
         patch("local_context.openai_client") as mock_client:
        result = get_local_highlight("user-1", None, None, None)

        assert result is None
        mock_interests.assert_not_called()
        mock_client.responses.create.assert_not_called()


def test_no_tracked_interests_short_circuits_without_calling_the_api():
    with patch("local_context.get_top_interests", return_value=[]), \
         patch("local_context.openai_client") as mock_client:
        result = get_local_highlight("user-1", "Rwanda", "Kigali", None)

        assert result is None
        mock_client.responses.create.assert_not_called()


def test_finds_a_real_current_match_and_passes_location_and_interests():
    with patch("local_context.get_top_interests", return_value=["arsenal"]), \
         patch("local_context.openai_client") as mock_client:
        mock_client.responses.create.return_value = MagicMock(
            output_text="Arsenal play tonight at 8pm local time."
        )

        result = get_local_highlight("user-1", "Rwanda", "Kigali", None)

        assert result == "Arsenal play tonight at 8pm local time."
        call_kwargs = mock_client.responses.create.call_args.kwargs
        tool = call_kwargs["tools"][0]
        assert tool["type"] == "web_search"
        assert tool["user_location"] == {
            "type": "approximate",
            "country": "Rwanda",
            "region": "Kigali",
            "city": None,
        }
        assert "arsenal" in call_kwargs["input"]
        assert "Rwanda" in call_kwargs["input"]


def test_none_response_returns_none():
    with patch("local_context.get_top_interests", return_value=["arsenal"]), \
         patch("local_context.openai_client") as mock_client:
        mock_client.responses.create.return_value = MagicMock(output_text="NONE")

        assert get_local_highlight("user-1", "Rwanda", None, None) is None


def test_empty_response_returns_none():
    with patch("local_context.get_top_interests", return_value=["arsenal"]), \
         patch("local_context.openai_client") as mock_client:
        mock_client.responses.create.return_value = MagicMock(output_text="   ")

        assert get_local_highlight("user-1", "Rwanda", None, None) is None


def test_api_exception_returns_none_not_raises():
    with patch("local_context.get_top_interests", return_value=["arsenal"]), \
         patch("local_context.openai_client") as mock_client:
        mock_client.responses.create.side_effect = Exception("boom")

        assert get_local_highlight("user-1", "Rwanda", None, None) is None
