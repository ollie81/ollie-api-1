# ============================================================
# Tests for OllieDB.request_account_deletion / delete_account and
# module-level purge_expired_account_deletions -- the grace-period
# deletion flow backing POST /settings/delete-account. delete_account
# is explicit per-table (doesn't rely on Supabase FK cascades, which
# were never verified for several tables here -- see its own
# docstring), so these tests exist mainly to lock in that every
# known user-owned table actually gets cleared, and that one table
# failing doesn't stop the rest.
# ============================================================

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from database import (
    OllieDB,
    ACCOUNT_DELETION_GRACE_DAYS,
    purge_expired_account_deletions,
)

ALL_CHILD_TABLES = (
    "conversations", "crisis_flags", "goals", "memories",
    "message_usage", "moderation_flags", "moods", "notifications",
    "refresh_tokens", "scheduled_events", "sessions",
    "subscriptions", "user_interests", "voice_usage",
)


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


def test_request_account_deletion_stamps_the_user_row():
    with patch("database.supabase") as mock_supabase:
        OllieDB().request_account_deletion("user-1")

        mock_supabase.table.assert_called_with("users")
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert "deletion_requested_at" in update_call
        # A real, parseable timestamp -- not a placeholder.
        datetime.fromisoformat(update_call["deletion_requested_at"])
        mock_supabase.table.return_value.update.return_value.eq.assert_called_once_with("id", "user-1")


def test_request_account_deletion_returns_the_grace_period_end():
    with patch("database.supabase"):
        scheduled_for = OllieDB().request_account_deletion("user-1")

        parsed = datetime.fromisoformat(scheduled_for)
        expected = datetime.now(timezone.utc) + timedelta(days=ACCOUNT_DELETION_GRACE_DAYS)
        assert abs((parsed - expected).total_seconds()) < 5


def test_delete_account_clears_every_known_child_table():
    with patch("database.supabase") as mock_supabase:
        OllieDB().delete_account("user-1")

        called_tables = [c.args[0] for c in mock_supabase.table.call_args_list]
        for table in ALL_CHILD_TABLES:
            assert table in called_tables, f"{table} was never cleared"
        assert "users" in called_tables


def test_delete_account_deletes_the_users_row_last_and_by_id():
    with patch("database.supabase") as mock_supabase:
        OllieDB().delete_account("user-1")

        called_tables = [c.args[0] for c in mock_supabase.table.call_args_list]
        assert called_tables[-1] == "users"
        mock_supabase.table.return_value.delete.return_value.eq.assert_any_call("id", "user-1")


def test_delete_account_scopes_child_deletes_to_user_id():
    with patch("database.supabase") as mock_supabase:
        OllieDB().delete_account("user-42")

        mock_supabase.table.return_value.delete.return_value.eq.assert_any_call("user_id", "user-42")


def test_delete_account_survives_a_failing_table_and_still_deletes_users():
    with patch("database.supabase") as mock_supabase:
        crisis_mock = MagicMock()
        crisis_mock.delete.return_value.eq.return_value.execute.side_effect = Exception("table missing")

        def table_side_effect(name):
            return crisis_mock if name == "crisis_flags" else MagicMock()

        mock_supabase.table.side_effect = table_side_effect

        OllieDB().delete_account("user-1")  # must not raise

        users_calls = [c for c in mock_supabase.table.call_args_list if c.args == ("users",)]
        assert len(users_calls) == 1


def test_purge_calls_delete_account_for_each_due_row():
    with patch("database.supabase") as mock_supabase, \
         patch("database.OllieDB.delete_account") as mock_delete:
        chain = mock_supabase.table.return_value.select.return_value.not_.is_.return_value.lte.return_value
        chain.execute.return_value = _mock_result([{"id": "user-1"}, {"id": "user-2"}])

        purge_expired_account_deletions()

        assert mock_delete.call_count == 2
        mock_delete.assert_any_call("user-1")
        mock_delete.assert_any_call("user-2")


def test_purge_does_nothing_when_none_are_due():
    with patch("database.supabase") as mock_supabase, \
         patch("database.OllieDB.delete_account") as mock_delete:
        chain = mock_supabase.table.return_value.select.return_value.not_.is_.return_value.lte.return_value
        chain.execute.return_value = _mock_result([])

        purge_expired_account_deletions()

        mock_delete.assert_not_called()
