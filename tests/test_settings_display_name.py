# ============================================================
# Tests for settings.update_display_name (PUT /settings/display-name).
# Backs onboarding's "what should Ollie call you?" step for phone
# and email signups. Same direct-call style as
# test_settings_notification_frequency.py.
# ============================================================

from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

from settings import update_display_name, DisplayNameRequest


def _run(name, user_id="user-1"):
    with patch("settings.supabase") as mock_supabase:
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        result = update_display_name(DisplayNameRequest(name=name), current_user={"id": user_id})
        return result, mock_supabase


def test_success_returns_the_trimmed_name():
    result, _ = _run("  Olivia  ")
    assert result == {"success": True, "username": "Olivia"}


def test_writes_scoped_to_the_current_user():
    _, mock_supabase = _run("Olivia", user_id="user-42")
    update_call = mock_supabase.table.return_value.update.call_args[0][0]
    assert update_call["username"] == "Olivia"
    mock_supabase.table.return_value.update.return_value.eq.assert_called_once_with("id", "user-42")


def test_empty_name_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        update_display_name(DisplayNameRequest(name="   "), current_user={"id": "user-1"})
    assert exc_info.value.status_code == 400


def test_name_is_truncated_to_fifty_characters():
    long_name = "x" * 80
    result, _ = _run(long_name)
    assert len(result["username"]) == 50
