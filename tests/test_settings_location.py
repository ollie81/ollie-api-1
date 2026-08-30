# ============================================================
# Tests for settings.update_location (PUT /location) -- lets a
# user set country/region/district so Ollie can talk like a
# local (see chat.py's _location_block). Same direct-call style
# as test_settings_usage.py.
# ============================================================

from unittest.mock import patch, MagicMock

from settings import update_location, LocationUpdateRequest


def _run(country=None, region=None, district=None, user_id="user-1"):
    with patch("settings.supabase") as mock_supabase:
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        result = update_location(
            LocationUpdateRequest(country=country, region=region, district=district),
            current_user={"id": user_id},
        )
        return result, mock_supabase


def test_success_returns_the_saved_values():
    result, _ = _run(country="Rwanda", region="Kigali", district="Kicukiro")
    assert result["success"] is True
    assert result["country"] == "Rwanda"
    assert result["region"] == "Kigali"
    assert result["district"] == "Kicukiro"


def test_writes_all_three_fields_scoped_to_the_current_user():
    _, mock_supabase = _run(country="Rwanda", region="Kigali", district="Kicukiro", user_id="user-42")

    update_call = mock_supabase.table.return_value.update.call_args[0][0]
    assert update_call == {"country": "Rwanda", "region": "Kigali", "district": "Kicukiro"}
    mock_supabase.table.return_value.update.return_value.eq.assert_called_once_with("id", "user-42")


def test_partial_update_leaves_the_others_null():
    result, mock_supabase = _run(country="Rwanda")
    assert result["country"] == "Rwanda"
    assert result["region"] is None
    assert result["district"] is None

    update_call = mock_supabase.table.return_value.update.call_args[0][0]
    assert update_call == {"country": "Rwanda", "region": None, "district": None}
