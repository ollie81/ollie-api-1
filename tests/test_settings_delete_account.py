# ============================================================
# Tests for settings.request_delete_account (POST /settings/delete-
# account) -- schedules deletion rather than performing it, and the
# confirmation phrase is checked server-side, not just trusted from
# a client-side gate. See test_account_deletion_db.py for the actual
# deletion/grace-period mechanics this route delegates to.
# ============================================================

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from settings import request_delete_account, DeleteAccountRequest


def test_wrong_confirmation_is_rejected_with_no_db_call():
    with patch("settings.OllieDB") as mock_db_cls:
        with pytest.raises(HTTPException) as exc_info:
            request_delete_account(DeleteAccountRequest(confirmation="delete"), current_user={"id": "user-1"})

        assert exc_info.value.status_code == 400
        mock_db_cls.return_value.request_account_deletion.assert_not_called()


def test_blank_confirmation_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        request_delete_account(DeleteAccountRequest(confirmation=""), current_user={"id": "user-1"})
    assert exc_info.value.status_code == 400


def test_correct_confirmation_schedules_deletion():
    with patch("settings.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.request_account_deletion.return_value = "2026-09-13T00:00:00+00:00"

        result = request_delete_account(DeleteAccountRequest(confirmation="DELETE"), current_user={"id": "user-1"})

        assert result == {"success": True, "scheduled_for": "2026-09-13T00:00:00+00:00"}
        mock_db_cls.return_value.request_account_deletion.assert_called_once_with("user-1")


def test_confirmation_is_trimmed_but_still_exact():
    with patch("settings.OllieDB") as mock_db_cls:
        mock_db_cls.return_value.request_account_deletion.return_value = "2026-09-13T00:00:00+00:00"

        result = request_delete_account(DeleteAccountRequest(confirmation="  DELETE  "), current_user={"id": "user-1"})

        assert result["success"] is True


def test_lowercase_confirmation_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        request_delete_account(DeleteAccountRequest(confirmation="delete my account"), current_user={"id": "user-1"})
    assert exc_info.value.status_code == 400
