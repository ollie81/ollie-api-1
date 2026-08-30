# ============================================================
# Tests for the new OllieDB methods added alongside the morning
# check-in / nightly recap redesign: get_memories_by_category,
# get_mood_for_date, get_messages_since.
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
