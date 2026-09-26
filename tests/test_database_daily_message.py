# ============================================================
# Tests for the new OllieDB methods added alongside the morning
# check-in / nightly recap redesign: get_memories_by_category,
# get_mood_for_date, get_messages_since. Also covers get_messages_
# between, save_conversation_summary, and get_recent_conversation_
# summaries, added alongside run_conversation_summaries.
# ============================================================

from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

from database import OllieDB


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


# ---- get_memories_by_category ----

def test_get_memories_by_category_returns_data():
    with patch("database.supabase") as mock_supabase:
        rows = [{"id": "m1", "memory_text": "has a test today", "category": "event"}]
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.order.return_value.limit.return_value.execute.return_value = \
            _mock_result(rows)
        result = OllieDB().get_memories_by_category("user-1", ["event"])
        assert result == rows


def test_get_memories_by_category_applies_since_filter():
    with patch("database.supabase") as mock_supabase:
        chain = mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.order.return_value.limit.return_value
        chain.gte.return_value.execute.return_value = _mock_result([])
        since = datetime.now(timezone.utc)

        OllieDB().get_memories_by_category("user-1", ["event"], since=since)

        chain.gte.assert_called_once_with("created_at", since.isoformat())


def test_get_memories_by_category_empty_when_no_data():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.order.return_value.limit.return_value.execute.return_value = \
            _mock_result(None)
        assert OllieDB().get_memories_by_category("user-1", ["event"]) == []


# ---- get_mood_for_date ----

def test_get_mood_for_date_returns_the_row():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"mood": "stressed", "date": "2026-01-01"}])
        result = OllieDB().get_mood_for_date("user-1", date(2026, 1, 1))
        assert result == {"mood": "stressed", "date": "2026-01-01"}


def test_get_mood_for_date_returns_none_when_no_row():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([])
        assert OllieDB().get_mood_for_date("user-1", date(2026, 1, 1)) is None


# ---- get_messages_since ----

def test_get_messages_since_returns_data_in_order():
    with patch("database.supabase") as mock_supabase:
        rows = [
            {"sender": "user", "message": "hey", "created_at": "2026-01-01T08:00:00+00:00"},
            {"sender": "ollie", "message": "hey!", "created_at": "2026-01-01T08:00:05+00:00"},
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value = \
            _mock_result(rows)
        result = OllieDB().get_messages_since("user-1", datetime.now(timezone.utc))
        assert result == rows


def test_get_messages_since_empty_when_no_data():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value = \
            _mock_result(None)
        assert OllieDB().get_messages_since("user-1", datetime.now(timezone.utc)) == []


# ---- get_messages_between ----

def test_get_messages_between_applies_both_bounds():
    with patch("database.supabase") as mock_supabase:
        rows = [{"sender": "user", "message": "hey", "created_at": "2026-01-01T08:00:00+00:00"}]
        chain = mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value
        chain.lt.return_value.order.return_value.limit.return_value.execute.return_value = _mock_result(rows)

        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 2, tzinfo=timezone.utc)
        result = OllieDB().get_messages_between("user-1", start, end)

        assert result == rows
        chain.lt.assert_called_once_with("created_at", end.isoformat())


def test_get_messages_between_empty_when_no_data():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value \
            .lt.return_value.order.return_value.limit.return_value.execute.return_value = _mock_result(None)
        result = OllieDB().get_messages_between(
            "user-1", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        assert result == []


# ---- save_conversation_summary ----

def test_save_conversation_summary_upserts_with_the_right_conflict_key():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.upsert.return_value.execute.return_value = _mock_result([])

        OllieDB().save_conversation_summary("user-1", date(2026, 1, 1), "talked about Viyo")

        upsert_call = mock_supabase.table.return_value.upsert.call_args
        assert upsert_call[0][0] == {
            "user_id": "user-1",
            "summary_date": "2026-01-01",
            "summary_text": "talked about Viyo",
        }
        assert upsert_call[1]["on_conflict"] == "user_id,summary_date"


# ---- get_recent_conversation_summaries ----

def test_get_recent_conversation_summaries_returns_data():
    with patch("database.supabase") as mock_supabase:
        rows = [{"summary_date": "2026-01-02", "summary_text": "talked about Viyo"}]
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = \
            _mock_result(rows)
        assert OllieDB().get_recent_conversation_summaries("user-1") == rows


def test_get_recent_conversation_summaries_empty_when_no_data():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = \
            _mock_result(None)
        assert OllieDB().get_recent_conversation_summaries("user-1") == []


# ---- get_user_context ----

def test_get_user_context_includes_recent_summaries():
    with patch("database.supabase") as mock_supabase, \
         patch.object(OllieDB, "get_relevant_memories", return_value=[]), \
         patch.object(OllieDB, "get_recent_conversation_summaries", return_value=[{"summary_date": "2026-01-01", "summary_text": "talked about Viyo"}]):
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([])

        context = OllieDB().get_user_context("user-1")

        assert context["recent_summaries"] == [{"summary_date": "2026-01-01", "summary_text": "talked about Viyo"}]
