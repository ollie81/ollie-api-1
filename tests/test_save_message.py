# ============================================================
# Tests for OllieDB.save_message's last_message_at stamping --
# backs the "you disappeared" re-engagement check (Phase 5). Only
# user-sent messages count as activity; Ollie's own replies must
# not touch it.
# ============================================================

from unittest.mock import patch

from database import OllieDB


def test_user_message_stamps_last_message_at():
    with patch("database.supabase") as mock_supabase:
        OllieDB().save_message("user-1", "session-1", "hey", "user")

        # Second table() call is the users-table stamp (first is the
        # conversations insert).
        assert mock_supabase.table.call_args_list[1][0][0] == "users"
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert "last_message_at" in update_call
        mock_supabase.table.return_value.update.return_value.eq.assert_called_once_with("id", "user-1")


def test_ollie_message_does_not_stamp_last_message_at():
    with patch("database.supabase") as mock_supabase:
        OllieDB().save_message("user-1", "session-1", "hey!", "ollie")

        # Only the conversations insert should have happened -- no
        # users-table update at all.
        assert mock_supabase.table.call_count == 1
        assert mock_supabase.table.call_args_list[0][0][0] == "conversations"
