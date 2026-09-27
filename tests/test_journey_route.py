# ============================================================
# Tests for journey.get_journey (GET /journey/) -- combines
# get_journey_summary with compute_relationship_stage. Same
# direct-call style as test_settings_usage.py.
# ============================================================

from datetime import date, timedelta
from unittest.mock import patch

from journey import get_journey, FREE_HIGHLIGHT_LIMIT, PREMIUM_HIGHLIGHT_LIMIT, HOME_HIGHLIGHT_MAX_AGE_DAYS


def _run(current_user, summary):
    with patch("journey.OllieDB") as mock_db_cls, \
         patch("journey.is_premium_active", return_value=False):
        mock_db_cls.return_value.get_journey_summary.return_value = summary
        return get_journey(current_user=current_user)


def test_new_user_gets_new_stage():
    result = _run(
        {"id": "user-1", "total_active_days": 0},
        {"memory_count": 0, "active_goals": [], "completed_goals": [], "highlights": []},
    )
    assert result["stage"] == "new"
    assert result["stage_label"] == "New"
    assert result["stage_emoji"] == "🌱"


def test_trusted_user_gets_trusted_stage():
    result = _run(
        {"id": "user-1", "total_active_days": 90},
        {"memory_count": 50, "active_goals": [], "completed_goals": [{"title": "x"}] * 5, "highlights": []},
    )
    assert result["stage"] == "trusted"
    assert result["stage_emoji"] == "⭐"


def test_missing_total_active_days_defaults_to_zero():
    result = _run(
        {"id": "user-1"},
        {"memory_count": 0, "active_goals": [], "completed_goals": [], "highlights": []},
    )
    assert result["active_days"] == 0
    assert result["stage"] == "new"


def test_accomplishment_count_derived_from_completed_goals_length():
    result = _run(
        {"id": "user-1", "total_active_days": 25},
        {"memory_count": 10, "active_goals": [], "completed_goals": [{"title": "a"}, {"title": "b"}], "highlights": []},
    )
    # depth = 10 + 2*2 = 14 -- just under the "close" threshold (15)
    assert result["stage"] == "getting_to_know_you"


def test_response_passes_through_goals_and_highlights():
    summary = {
        "memory_count": 3,
        "active_goals": [{"title": "run a marathon"}],
        "completed_goals": [{"title": "fix the login bug"}],
        "highlights": [{"memory_text": "Has a dog named Max", "category": "person"}],
    }
    result = _run({"id": "user-1", "total_active_days": 5}, summary)
    assert result["active_goals"] == summary["active_goals"]
    assert result["completed_goals"] == summary["completed_goals"]
    assert result["highlights"] == summary["highlights"]
    assert result["memory_count"] == 3


def test_db_failure_returns_500_not_raw_exception():
    from fastapi import HTTPException
    import pytest

    with patch("journey.OllieDB") as mock_db_cls, \
         patch("journey.is_premium_active", return_value=False):
        mock_db_cls.return_value.get_journey_summary.side_effect = Exception("db down")
        with pytest.raises(HTTPException) as exc_info:
            get_journey(current_user={"id": "user-1", "total_active_days": 0})
        assert exc_info.value.status_code == 500


# ---- Premium: deeper highlight limit + is_premium field ----

_EMPTY_SUMMARY = {"memory_count": 0, "active_goals": [], "completed_goals": [], "highlights": []}


def test_free_user_gets_free_highlight_limit_and_is_premium_false():
    with patch("journey.OllieDB") as mock_db_cls, \
         patch("journey.is_premium_active", return_value=False):
        mock_db_cls.return_value.get_journey_summary.return_value = _EMPTY_SUMMARY
        result = get_journey(current_user={"id": "user-1", "total_active_days": 0})

        assert result["is_premium"] is False
        mock_db_cls.return_value.get_journey_summary.assert_called_once_with(
            "user-1", highlight_limit=FREE_HIGHLIGHT_LIMIT,
        )


def test_premium_user_gets_deeper_highlight_limit_and_is_premium_true():
    with patch("journey.OllieDB") as mock_db_cls, \
         patch("journey.is_premium_active", return_value=True):
        mock_db_cls.return_value.get_journey_summary.return_value = _EMPTY_SUMMARY
        result = get_journey(current_user={"id": "user-1", "total_active_days": 0})

        assert result["is_premium"] is True
        mock_db_cls.return_value.get_journey_summary.assert_called_once_with(
            "user-1", highlight_limit=PREMIUM_HIGHLIGHT_LIMIT,
        )
        assert PREMIUM_HIGHLIGHT_LIMIT > FREE_HIGHLIGHT_LIMIT


# ---- home_highlight ----
# The live, personal line shown on the home screen -- see
# daily_message.py's _process_morning_checkin, which is what actually
# saves last_home_highlight_text/_date onto the user row.

def test_home_highlight_included_when_fresh():
    today = date.today()
    result = _run(
        {
            "id": "user-1", "total_active_days": 5,
            "last_home_highlight_text": "you said you had that test today -- how'd it go?",
            "last_home_highlight_date": today.isoformat(),
        },
        _EMPTY_SUMMARY,
    )
    assert result["home_highlight"] == "you said you had that test today -- how'd it go?"


def test_home_highlight_included_within_max_age():
    stale_but_ok = date.today() - timedelta(days=HOME_HIGHLIGHT_MAX_AGE_DAYS)
    result = _run(
        {
            "id": "user-1", "total_active_days": 5,
            "last_home_highlight_text": "still here for you",
            "last_home_highlight_date": stale_but_ok.isoformat(),
        },
        _EMPTY_SUMMARY,
    )
    assert result["home_highlight"] == "still here for you"


def test_home_highlight_omitted_when_too_stale():
    too_old = date.today() - timedelta(days=HOME_HIGHLIGHT_MAX_AGE_DAYS + 1)
    result = _run(
        {
            "id": "user-1", "total_active_days": 5,
            "last_home_highlight_text": "you said you had that test today",
            "last_home_highlight_date": too_old.isoformat(),
        },
        _EMPTY_SUMMARY,
    )
    assert result["home_highlight"] is None


def test_home_highlight_omitted_when_never_generated():
    result = _run({"id": "user-1", "total_active_days": 0}, _EMPTY_SUMMARY)
    assert result["home_highlight"] is None


def test_home_highlight_omitted_when_date_missing():
    result = _run(
        {"id": "user-1", "total_active_days": 5, "last_home_highlight_text": "hey!"},
        _EMPTY_SUMMARY,
    )
    assert result["home_highlight"] is None


def test_home_highlight_omitted_on_malformed_date():
    result = _run(
        {
            "id": "user-1", "total_active_days": 5,
            "last_home_highlight_text": "hey!", "last_home_highlight_date": "not-a-date",
        },
        _EMPTY_SUMMARY,
    )
    assert result["home_highlight"] is None
