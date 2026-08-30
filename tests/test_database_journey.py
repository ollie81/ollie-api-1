# ============================================================
# Tests for OllieDB.get_journey_summary -- the data behind
# "Our Space". Mocks database.supabase, same pattern as
# test_database_memory.py.
# ============================================================

from unittest.mock import patch, MagicMock

from database import OllieDB


def _mock_result(data, count=None):
    result = MagicMock()
    result.data = data
    result.count = count
    return result


def _patch_highlights(mock_supabase, data):
    # memories query chain: select().eq(user_id).eq(is_active).in_(category).order().order().limit().execute()
    mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.order.return_value.order.return_value.limit.return_value.execute.return_value = \
        _mock_result(data)


def test_memory_count_uses_exact_count_not_row_length():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result(None, count=57)
        mock_supabase.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value.execute.return_value = \
            _mock_result([])
        _patch_highlights(mock_supabase, [])

        summary = OllieDB().get_journey_summary("user-1")
        assert summary["memory_count"] == 57


def test_goals_split_into_active_and_completed():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result(None, count=0)
        goals = [
            {"id": "g1", "title": "run a marathon", "status": "active"},
            {"id": "g2", "title": "fix the login bug", "status": "completed", "completed_at": "2026-01-01T00:00:00+00:00"},
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value.execute.return_value = \
            _mock_result(goals)
        _patch_highlights(mock_supabase, [])

        summary = OllieDB().get_journey_summary("user-1")
        assert summary["active_goals"] == [goals[0]]
        assert summary["completed_goals"] == [goals[1]]


def test_highlights_returned():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result(None, count=0)
        mock_supabase.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value.execute.return_value = \
            _mock_result([])
        highlights = [{"id": "m1", "memory_text": "Got the login bug fixed", "category": "accomplishment"}]
        _patch_highlights(mock_supabase, highlights)

        summary = OllieDB().get_journey_summary("user-1")
        assert summary["highlights"] == highlights


def test_empty_results_default_to_empty_lists_and_zero():
    with patch("database.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result(None, count=None)
        mock_supabase.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value.execute.return_value = \
            _mock_result(None)
        _patch_highlights(mock_supabase, None)

        summary = OllieDB().get_journey_summary("user-1")
        assert summary == {
            "memory_count": 0,
            "active_goals": [],
            "completed_goals": [],
            "highlights": [],
        }
