# ============================================================
# Tests for the new OllieDB memory methods added alongside the
# categorized memory system: save_memory(category=...),
# get_all_memories, update_memory, delete_memory, complete_goal.
# database.supabase must be patched BEFORE constructing OllieDB(),
# since __init__ binds self.supabase from the module-level client
# at construction time.
# ============================================================

from unittest.mock import patch, MagicMock

from database import OllieDB


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


# ---- save_memory ----

def test_save_memory_includes_category_when_given():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        db = OllieDB()
        db.save_memory("user-1", "Has a dog named Max", importance=2, category="person")

        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert insert_call["category"] == "person"
        assert insert_call["memory_text"] == "Has a dog named Max"
        assert insert_call["importance"] == 2


def test_save_memory_omits_category_key_when_none():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        db = OllieDB()
        db.save_memory("user-1", "some fact", importance=1, category=None)

        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]
        assert "category" not in insert_call


def test_save_memory_skips_insert_on_duplicate():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "existing"}])
        db = OllieDB()
        db.save_memory("user-1", "already saved", importance=1, category="event")

        mock_supabase.table.return_value.insert.assert_not_called()


# ---- get_relevant_memories ----
# Importance-3 (major/identity-level) memories are exempt from
# `limit` -- see the comment on OllieDB.get_relevant_memories. This
# now runs as two separate queries (importance == 3, then
# importance < 3), each mocked independently below since .eq and
# .lt are distinct attributes on the shared mock chain. Stale
# "event" memories (older than EVENT_MEMORY_MAX_AGE_DAYS) are
# filtered out of either tier, regardless of importance.

def _major_chain(mock_supabase):
    return mock_supabase.table.return_value.select.return_value.eq.return_value \
        .eq.return_value.eq.return_value.order.return_value.execute

def _minor_chain(mock_supabase):
    return mock_supabase.table.return_value.select.return_value.eq.return_value \
        .eq.return_value.lt.return_value.order.return_value.order.return_value.limit.return_value.execute


def test_get_relevant_memories_returns_all_major_plus_top_minor():
    with patch("database.supabase") as mock_supabase:
        major_rows = [{"id": "m1", "importance": 3}, {"id": "m2", "importance": 3}]
        minor_rows = [{"id": "m3", "importance": 2}]
        _major_chain(mock_supabase).return_value = _mock_result(major_rows)
        _minor_chain(mock_supabase).return_value = _mock_result(minor_rows)

        db = OllieDB()
        result = db.get_relevant_memories("user-1", limit=3)

        assert result == major_rows + minor_rows


def test_get_relevant_memories_never_drops_major_memories_past_the_limit():
    with patch("database.supabase") as mock_supabase:
        # 5 major memories, but limit is only 3 -- all 5 must still come back.
        major_rows = [{"id": f"m{i}", "importance": 3} for i in range(5)]
        _major_chain(mock_supabase).return_value = _mock_result(major_rows)

        db = OllieDB()
        result = db.get_relevant_memories("user-1", limit=3)

        assert result == major_rows
        # No slots left for the minor tier, so it's never even queried.
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.lt.assert_not_called()


def test_get_relevant_memories_drops_stale_event_memory_even_when_major():
    with patch("database.supabase") as mock_supabase:
        major_rows = [
            {"id": "m1", "importance": 3, "category": "event", "created_at": "2020-01-01T00:00:00+00:00"},
            {"id": "m2", "importance": 3, "category": "identity"},
        ]
        _major_chain(mock_supabase).return_value = _mock_result(major_rows)
        _minor_chain(mock_supabase).return_value = _mock_result([])

        db = OllieDB()
        result = db.get_relevant_memories("user-1", limit=5)

        assert result == [major_rows[1]]  # the stale event memory is dropped despite importance 3


def test_get_relevant_memories_keeps_fresh_event_memory():
    from datetime import datetime, timezone
    with patch("database.supabase") as mock_supabase:
        fresh_row = {
            "id": "m1", "importance": 1, "category": "event",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _major_chain(mock_supabase).return_value = _mock_result([])
        _minor_chain(mock_supabase).return_value = _mock_result([fresh_row])

        db = OllieDB()
        result = db.get_relevant_memories("user-1", limit=5)

        assert result == [fresh_row]


# ---- get_all_memories ----

def test_get_all_memories_returns_data():
    with patch("database.supabase") as mock_supabase:
        rows = [{"id": "m1", "memory_text": "fact one"}, {"id": "m2", "memory_text": "fact two"}]
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = \
            _mock_result(rows)
        db = OllieDB()
        assert db.get_all_memories("user-1") == rows


def test_get_all_memories_empty_when_no_data():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = \
            _mock_result(None)
        db = OllieDB()
        assert db.get_all_memories("user-1") == []


# ---- update_memory ----

def test_update_memory_scoped_to_user_and_returns_true_on_match():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "m1"}])
        db = OllieDB()
        assert db.update_memory("user-1", "m1", memory_text="corrected text") is True

        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["memory_text"] == "corrected text"
        mock_supabase.table.return_value.update.return_value.eq.assert_called_with("id", "m1")


def test_update_memory_returns_false_when_no_row_matched():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        db = OllieDB()
        assert db.update_memory("user-1", "not-mine", memory_text="x") is False


def test_update_memory_only_includes_provided_fields():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "m1"}])
        db = OllieDB()
        db.update_memory("user-1", "m1", category="struggle")

        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["category"] == "struggle"
        assert "memory_text" not in update_call


# ---- delete_memory ----

def test_delete_memory_scoped_to_user_and_returns_true_on_match():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "m1"}])
        db = OllieDB()
        assert db.delete_memory("user-1", "m1") is True


def test_delete_memory_returns_false_when_no_row_matched():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        db = OllieDB()
        assert db.delete_memory("user-1", "not-mine") is False


# ---- complete_goal ----

def test_complete_goal_returns_true_on_match():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"id": "g1"}])
        db = OllieDB()
        assert db.complete_goal("user-1", "fix the login bug") is True

        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        assert update_call["status"] == "completed"
        assert "completed_at" in update_call


def test_complete_goal_returns_false_when_no_active_goal_matches():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        db = OllieDB()
        assert db.complete_goal("user-1", "not a real goal") is False
