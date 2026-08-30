# ============================================================
# Tests for settings.update_notification_frequency (PUT
# /notification-frequency). Same direct-call style as
# test_settings_location.py.
# ============================================================

from unittest.mock import patch, MagicMock

import pytest
from pydantic import ValidationError

from settings import update_notification_frequency, NotificationFrequencyRequest, get_usage


def _run(frequency, user_id="user-1"):
    with patch("settings.supabase") as mock_supabase:
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        result = update_notification_frequency(
            NotificationFrequencyRequest(frequency=frequency),
            current_user={"id": user_id},
        )
        return result, mock_supabase


def test_success_returns_the_frequency():
    result, _ = _run("frequent")
    assert result == {"success": True, "notification_frequency": "frequent"}


def test_writes_frequency_scoped_to_the_current_user():
    _, mock_supabase = _run("low", user_id="user-42")
    update_call = mock_supabase.table.return_value.update.call_args[0][0]
    assert update_call["notification_frequency"] == "low"
    mock_supabase.table.return_value.update.return_value.eq.assert_called_once_with("id", "user-42")


def test_off_also_flips_notifications_enabled_false():
    _, mock_supabase = _run("off")
    update_call = mock_supabase.table.return_value.update.call_args[0][0]
    assert update_call["notifications_enabled"] is False


def test_non_off_frequency_sets_notifications_enabled_true():
    for freq in ("low", "normal", "frequent"):
        _, mock_supabase = _run(freq)
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["notifications_enabled"] is True


def test_invalid_frequency_is_rejected_by_validation():
    with pytest.raises(ValidationError):
        NotificationFrequencyRequest(frequency="sometimes")


# ---- /usage exposes notification_frequency ----

def _run_usage(user_row):
    with patch("settings.OllieDB") as mock_db_cls, \
         patch("settings.supabase") as mock_supabase:
        mock_db_cls.return_value.get_messages_today.return_value = 0
        mock_db_cls.return_value.has_active_ad_bonus.return_value = False
        mock_db_cls.return_value.get_streak.return_value = 0
        mock_db_cls.return_value.get_voice_trial_remaining.return_value = 0
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[])
        return get_usage(current_user=user_row)


def test_usage_returns_stored_frequency():
    result = _run_usage({"id": "user-1", "notification_frequency": "frequent"})
    assert result["notification_frequency"] == "frequent"


def test_usage_defaults_to_normal_when_unset():
    result = _run_usage({"id": "user-1"})
    assert result["notification_frequency"] == "normal"
