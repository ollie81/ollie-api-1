# ============================================================
# Tests for OllieDB.search_messages / get_messages_by_ids (backs
# GET /chat/search and /history's reply-to preview resolution), and
# save_message's new return value + reply_to_id passthrough (backs
# "reply to a message"). last_message_at stamping itself stays
# covered by test_save_message.py -- not repeated here.
# ============================================================

from unittest.mock import patch, MagicMock

from database import OllieDB


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


def test_search_messages_filters_by_user_and_query():
    with patch("database.supabase") as mock_supabase:
        chain = mock_supabase.table.return_value.select.return_value.eq.return_value.ilike.return_value.order.return_value.limit.return_value
        chain.execute.return_value = _mock_result([
            {"id": "m1", "sender": "ollie", "message": "let's talk about your exam", "created_at": "2026-08-01T00:00:00+00:00"},
        ])

        results = OllieDB().search_messages("user-1", "exam", limit=50)

        assert results[0]["id"] == "m1"
        mock_supabase.table.return_value.select.return_value.eq.assert_called_once_with("user_id", "user-1")
        mock_supabase.table.return_value.select.return_value.eq.return_value.ilike.assert_called_once_with("message", "%exam%")


def test_get_messages_by_ids_returns_empty_dict_for_empty_input():
    with patch("database.supabase") as mock_supabase:
        result = OllieDB().get_messages_by_ids([])

        assert result == {}
        mock_supabase.table.assert_not_called()


def test_get_messages_by_ids_keys_by_id():
    with patch("database.supabase") as mock_supabase:
        chain = mock_supabase.table.return_value.select.return_value.in_.return_value
        chain.execute.return_value = _mock_result([
            {"id": "m1", "sender": "user", "message": "hey"},
            {"id": "m2", "sender": "ollie", "message": "hi there"},
        ])

        result = OllieDB().get_messages_by_ids(["m1", "m2"])

        assert result["m1"]["message"] == "hey"
        assert result["m2"]["sender"] == "ollie"


def test_save_message_returns_new_id():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.insert.return_value.execute.return_value = \
            _mock_result([{"id": "new-msg-id"}])

        new_id = OllieDB().save_message("user-1", "session-1", "hey", "user")

        assert new_id == "new-msg-id"


def test_save_message_includes_reply_to_id_in_insert_payload():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.insert.return_value.execute.return_value = _mock_result([{"id": "x"}])

        OllieDB().save_message("user-1", "session-1", "about that...", "user", reply_to_id="earlier-id")

        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert insert_call["reply_to_id"] == "earlier-id"


def test_save_message_defaults_reply_to_id_to_none():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.insert.return_value.execute.return_value = _mock_result([{"id": "x"}])

        OllieDB().save_message("user-1", "session-1", "hey", "ollie", 0.0)

        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert insert_call["reply_to_id"] is None
