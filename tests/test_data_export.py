# ============================================================
# Tests for OllieDB.export_user_data (the "download your data"
# counterpart to delete_account -- see test_account_deletion_db.py)
# and settings.export_data (GET /settings/export-data). Focus:
# sensitive/internal fields never leak into the export, and every
# included category is actually scoped to the requesting user.
# ============================================================

from unittest.mock import patch, MagicMock

from database import OllieDB
from settings import export_data


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


SENSITIVE_PROFILE_FIELDS = (
    "password_hash", "email_otp_hash", "email_otp_expires_at",
    "deletion_requested_at", "fcm_token",
)


def _table_router(mock_supabase, rows_by_table):
    def table_side_effect(name):
        m = MagicMock()
        m.select.return_value.eq.return_value.execute.return_value = _mock_result(rows_by_table.get(name, []))
        m.select.return_value.eq.return_value.eq.return_value.execute.return_value = _mock_result(rows_by_table.get(name, []))
        return m
    mock_supabase.table.side_effect = table_side_effect


def test_export_strips_sensitive_profile_fields():
    with patch("database.supabase") as mock_supabase:
        profile_row = {
            "id": "user-1",
            "username": "Olivia",
            "password_hash": "secret-hash",
            "email_otp_hash": "otp-hash",
            "email_otp_expires_at": "2026-08-20T00:00:00+00:00",
            "deletion_requested_at": None,
            "fcm_token": "some-device-token",
        }
        _table_router(mock_supabase, {"users": [profile_row]})

        result = OllieDB().export_user_data("user-1")

        for field in SENSITIVE_PROFILE_FIELDS:
            assert field not in result["profile"], f"{field} leaked into export"
        assert result["profile"]["username"] == "Olivia"


def test_export_includes_every_expected_category():
    with patch("database.supabase") as mock_supabase:
        _table_router(mock_supabase, {"users": [{"id": "user-1"}]})

        result = OllieDB().export_user_data("user-1")

        for category in (
            "profile", "conversations", "memories", "goals", "moods",
            "interests", "scheduled_events", "subscriptions", "notifications",
        ):
            assert category in result


def test_export_scopes_conversations_to_the_requesting_user():
    with patch("database.supabase") as mock_supabase:
        _table_router(mock_supabase, {
            "users": [{"id": "user-1"}],
            "conversations": [{"message": "hey", "user_id": "user-1"}],
        })

        result = OllieDB().export_user_data("user-1")

        assert result["conversations"] == [{"message": "hey", "user_id": "user-1"}]
        mock_supabase.table.assert_any_call("conversations")


def test_export_only_includes_active_memories():
    with patch("database.supabase") as mock_supabase:
        memories_mock = MagicMock()
        memories_mock.select.return_value.eq.return_value.eq.return_value.execute.return_value = _mock_result(
            [{"memory_text": "likes hiking"}]
        )

        def table_side_effect(name):
            if name == "memories":
                return memories_mock
            m = MagicMock()
            m.select.return_value.eq.return_value.execute.return_value = _mock_result(
                [{"id": "user-1"}] if name == "users" else []
            )
            return m

        mock_supabase.table.side_effect = table_side_effect

        OllieDB().export_user_data("user-1")

        memories_mock.select.return_value.eq.return_value.eq.assert_called_once_with("is_active", True)


def test_export_handles_a_missing_user_row_gracefully():
    with patch("database.supabase") as mock_supabase:
        _table_router(mock_supabase, {"users": []})

        result = OllieDB().export_user_data("user-1")

        assert result["profile"] == {}


def test_export_data_route_includes_exported_at_and_spreads_categories():
    with patch("settings.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.export_user_data.return_value = {
            "profile": {"username": "Olivia"},
            "conversations": [],
        }

        result = export_data(current_user={"id": "user-1"})

        assert "exported_at" in result
        assert result["profile"] == {"username": "Olivia"}
        mock_db_cls.return_value.export_user_data.assert_called_once_with("user-1")
