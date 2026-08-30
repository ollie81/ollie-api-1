# ============================================================
# Tests for the new per-memory settings routes: GET /memories,
# PATCH /memories/{id}, DELETE /memories/{id}, PUT /memory/enabled.
# Same direct-call style as test_settings_usage.py /
# test_settings_location.py -- current_user is a plain dict by the
# time it reaches the function body.
# ============================================================

from unittest.mock import patch, MagicMock

from settings import list_memories, update_memory, delete_single_memory, toggle_memory, MemoryUpdateRequest, MemoryToggleRequest


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


# ---- list_memories ----

def test_list_memories_returns_the_users_memories():
    with patch("settings.OllieDB") as mock_db_cls:
        rows = [{"id": "m1", "memory_text": "loves hiking", "category": "preference"}]
        mock_db_cls.return_value.get_all_memories.return_value = rows
        result = list_memories(current_user={"id": "user-1"})
        assert result == {"memories": rows}
        mock_db_cls.return_value.get_all_memories.assert_called_once_with("user-1")


# ---- update_memory (route) ----

def test_update_memory_route_success():
    with patch("settings.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.update_memory.return_value = True
        result = update_memory("m1", MemoryUpdateRequest(memory_text="corrected"), current_user={"id": "user-1"})
        assert result == {"success": True}
        mock_db_cls.return_value.update_memory.assert_called_once_with(
            "user-1", "m1", memory_text="corrected", category=None
        )


def test_update_memory_route_404_when_not_found():
    from fastapi import HTTPException
    import pytest

    with patch("settings.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.update_memory.return_value = False
        with pytest.raises(HTTPException) as exc_info:
            update_memory("not-mine", MemoryUpdateRequest(memory_text="x"), current_user={"id": "user-1"})
        assert exc_info.value.status_code == 404


# ---- delete_single_memory ----

def test_delete_single_memory_success():
    with patch("settings.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.delete_memory.return_value = True
        result = delete_single_memory("m1", current_user={"id": "user-1"})
        assert result == {"success": True}


def test_delete_single_memory_404_when_not_found():
    from fastapi import HTTPException
    import pytest

    with patch("settings.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.delete_memory.return_value = False
        with pytest.raises(HTTPException) as exc_info:
            delete_single_memory("not-mine", current_user={"id": "user-1"})
        assert exc_info.value.status_code == 404


# ---- toggle_memory ----

def test_toggle_memory_off():
    with patch("settings.supabase") as mock_supabase:
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        result = toggle_memory(MemoryToggleRequest(enabled=False), current_user={"id": "user-1"})
        assert result == {"success": True, "memory_enabled": False}
        mock_supabase.table.return_value.update.assert_called_once_with({"memory_enabled": False})


def test_toggle_memory_on():
    with patch("settings.supabase") as mock_supabase:
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        result = toggle_memory(MemoryToggleRequest(enabled=True), current_user={"id": "user-1"})
        assert result == {"success": True, "memory_enabled": True}
