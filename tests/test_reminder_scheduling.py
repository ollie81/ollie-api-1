# ============================================================
# Tests for event_scheduler._compute_reminder_datetime — the
# deterministic date-math that replaced asking a fast LLM to
# compute "minutes from now" directly (which was landing
# reminders at the wrong time, including in the past).
# ============================================================

from datetime import datetime, timezone

from event_scheduler import _compute_reminder_datetime


def test_relative_minutes():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    result = _compute_reminder_datetime(
        {"time_type": "relative", "relative_minutes": 20}, utc_offset_minutes=0, now_utc=now
    )
    assert result == datetime(2026, 8, 24, 12, 20, tzinfo=timezone.utc)


def test_absolute_not_yet_passed_positive_offset():
    # now = 12:00 UTC = 14:00 local (UTC+2, e.g. Rwanda). "5pm" (17:00
    # local) hasn't happened yet today.
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    result = _compute_reminder_datetime(
        {"time_type": "absolute", "absolute_hour": 17, "absolute_minute": 0, "days_from_today": 0},
        utc_offset_minutes=120,
        now_utc=now,
    )
    assert result == datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)


def test_absolute_already_passed_rolls_to_tomorrow_positive_offset():
    # This is the exact scenario that used to land reminders in the
    # past: now = 16:00 UTC = 18:00 local (UTC+2). "5pm" (17:00 local)
    # already happened today — must roll to tomorrow, not fire
    # immediately or in the past.
    now = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)
    result = _compute_reminder_datetime(
        {"time_type": "absolute", "absolute_hour": 17, "absolute_minute": 0, "days_from_today": 0},
        utc_offset_minutes=120,
        now_utc=now,
    )
    assert result == datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc)
    assert result > now


def test_absolute_explicit_future_day_does_not_double_roll():
    # "tomorrow at 9am" (days_from_today=1) — must land exactly one
    # day out, not two, even though the auto-roll logic exists.
    now = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)  # 10:00 local
    result = _compute_reminder_datetime(
        {"time_type": "absolute", "absolute_hour": 9, "absolute_minute": 0, "days_from_today": 1},
        utc_offset_minutes=120,
        now_utc=now,
    )
    assert result == datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc)


def test_absolute_not_yet_passed_negative_offset():
    # now = 12:00 UTC = 07:00 local (UTC-5, e.g. US Eastern). "8am"
    # hasn't happened yet.
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    result = _compute_reminder_datetime(
        {"time_type": "absolute", "absolute_hour": 8, "absolute_minute": 0, "days_from_today": 0},
        utc_offset_minutes=-300,
        now_utc=now,
    )
    assert result == datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)


def test_absolute_already_passed_rolls_to_tomorrow_negative_offset():
    # now = 12:00 UTC = 07:00 local (UTC-5). "6am" already passed today.
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    result = _compute_reminder_datetime(
        {"time_type": "absolute", "absolute_hour": 6, "absolute_minute": 0, "days_from_today": 0},
        utc_offset_minutes=-300,
        now_utc=now,
    )
    assert result == datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc)
    assert result > now


def test_absolute_unknown_timezone_falls_back_to_utc():
    now = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    result = _compute_reminder_datetime(
        {"time_type": "absolute", "absolute_hour": 15, "absolute_minute": 0, "days_from_today": 0},
        utc_offset_minutes=None,
        now_utc=now,
    )
    assert result == datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)


def test_relative_missing_minutes_returns_none():
    now = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    result = _compute_reminder_datetime({"time_type": "relative"}, utc_offset_minutes=0, now_utc=now)
    assert result is None


def test_absolute_missing_hour_returns_none():
    now = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    result = _compute_reminder_datetime(
        {"time_type": "absolute", "absolute_hour": None, "absolute_minute": 0},
        utc_offset_minutes=0,
        now_utc=now,
    )
    assert result is None


def test_absolute_out_of_range_hour_returns_none():
    now = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    result = _compute_reminder_datetime(
        {"time_type": "absolute", "absolute_hour": 25, "absolute_minute": 0, "days_from_today": 0},
        utc_offset_minutes=0,
        now_utc=now,
    )
    assert result is None


def test_unknown_time_type_returns_none():
    now = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    result = _compute_reminder_datetime({"time_type": "bogus"}, utc_offset_minutes=0, now_utc=now)
    assert result is None
