# ============================================================
# Tests for auth._check_age_gate.
# ============================================================
# Dates are computed relative to today rather than hardcoded, so
# these keep passing regardless of when the suite runs.

from datetime import date

from auth import _check_age_gate, MIN_SIGNUP_AGE_YEARS


def _birthdate_years_ago(years: int) -> str:
    today = date.today()
    try:
        dob = today.replace(year=today.year - years)
    except ValueError:
        # today is Feb 29 and the target year isn't a leap year
        dob = today.replace(year=today.year - years, day=28)
    return dob.isoformat()


def test_well_over_minimum_age_is_allowed():
    assert _check_age_gate(_birthdate_years_ago(25)) is None


def test_well_under_minimum_age_is_blocked():
    error = _check_age_gate(_birthdate_years_ago(10))
    assert error is not None
    assert str(MIN_SIGNUP_AGE_YEARS) in error


def test_missing_date_of_birth_is_allowed():
    # Older app builds don't send this field at all -- absence must
    # never block signup, or every existing user would be locked out.
    assert _check_age_gate(None) is None


def test_empty_string_is_allowed():
    assert _check_age_gate("") is None


def test_malformed_date_is_blocked_not_raised():
    error = _check_age_gate("not-a-date")
    assert error is not None
    assert "format" in error.lower()


def test_garbage_input_does_not_silently_bypass():
    # A malformed value must be rejected outright, not treated the
    # same as "no date provided" -- otherwise it'd be a trivial way
    # to dodge the age check.
    assert _check_age_gate("banana") is not None
