# ============================================================
# Tests for settings.get_usage -- in particular notifications_enabled,
# which the client previously had no way to see: the field never
# appeared in this response, so Settings always showed the toggle
# as "on" regardless of what was actually stored.
#
# get_usage takes current_user via FastAPI's Depends, but that's
# just a plain dict by the time it reaches the function body, so it
# can be called directly with a hand-built dict -- same approach
# the rest of this suite uses to unit-test route functions.
# ============================================================

from unittest.mock import patch, MagicMock

from settings import get_usage


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


def _run(user_row, subscription_rows=None):
    with patch("settings.OllieDB") as mock_db_cls, \
         patch("settings.supabase") as mock_supabase:
        mock_db_cls.return_value.get_messages_today.return_value = 3
        mock_db_cls.return_value.has_active_ad_bonus.return_value = False
        mock_db_cls.return_value.get_streak.return_value = 2
        mock_db_cls.return_value.get_voice_trial_remaining.return_value = 42
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result(subscription_rows or [])
        return get_usage(current_user=user_row)


def test_notifications_enabled_true_when_set_true():
    result = _run({"id": "user-1", "notifications_enabled": True})
    assert result["notifications_enabled"] is True


def test_notifications_enabled_true_when_unset():
    # Existing users who've never touched the toggle have no value
    # at all -- must default to "on" to match actual push behavior
    # (NotificationService.create_notification uses the same default).
    result = _run({"id": "user-1"})
    assert result["notifications_enabled"] is True


def test_notifications_enabled_false_when_explicitly_disabled():
    result = _run({"id": "user-1", "notifications_enabled": False})
    assert result["notifications_enabled"] is False


def test_other_usage_fields_still_present():
    result = _run({"id": "user-1"}, subscription_rows=[{"id": "sub-1"}])
    assert result["messages_used_today"] == 3
    assert result["daily_limit"] == 20
    assert result["has_active_ad_bonus"] is False
    assert result["current_streak"] == 2
    assert result["voice_trial_seconds_remaining"] == 42
    assert result["is_premium"] is True
