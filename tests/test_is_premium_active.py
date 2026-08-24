# ============================================================
# Tests for premium.is_premium_active — the real premium gate,
# now also used to gate voice (see chat.py). Mocks the supabase
# client and, where relevant, Play verification.
# ============================================================

import time
from unittest.mock import patch, MagicMock

from premium import is_premium_active


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


def _patch_subscription_row(mock_supabase, row):
    mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
        _mock_result([row] if row else [])


def test_no_subscription_is_not_premium():
    with patch("premium.supabase") as mock_supabase:
        _patch_subscription_row(mock_supabase, None)
        assert is_premium_active("user-1") is False


def test_active_subscription_no_expiry_on_record_is_premium():
    with patch("premium.supabase") as mock_supabase:
        _patch_subscription_row(mock_supabase, {
            "id": "sub-1", "status": "active", "expiry_time_millis": 0,
        })
        assert is_premium_active("user-1") is True


def test_active_subscription_not_yet_expired_is_premium():
    future_ms = int(time.time() * 1000) + 1000 * 60 * 60 * 24
    with patch("premium.supabase") as mock_supabase:
        _patch_subscription_row(mock_supabase, {
            "id": "sub-1", "status": "active", "expiry_time_millis": future_ms,
        })
        assert is_premium_active("user-1") is True


def test_expired_and_play_not_configured_fails_open():
    # GOOGLE_PLAY_SERVICE_ACCOUNT_JSON isn't set in the test env,
    # so _get_play_service() raises -- must fail open rather than
    # strand a paying user over a config/infra issue.
    with patch("premium.supabase") as mock_supabase:
        _patch_subscription_row(mock_supabase, {
            "id": "sub-1", "status": "active", "expiry_time_millis": 1000,
            "product_id": "p1", "purchase_token": "tok",
        })
        assert is_premium_active("user-1") is True


def test_expired_locally_but_play_confirms_still_active_renewed():
    future_ms = int(time.time() * 1000) + 1000 * 60 * 60 * 24
    with patch("premium.supabase") as mock_supabase, \
         patch("premium._get_play_service") as mock_get_service:
        _patch_subscription_row(mock_supabase, {
            "id": "sub-1", "status": "active", "expiry_time_millis": 1000,
            "product_id": "p1", "purchase_token": "tok",
        })
        mock_service = MagicMock()
        mock_service.purchases.return_value.subscriptions.return_value.get.return_value.execute.return_value = {
            "paymentState": 1, "expiryTimeMillis": str(future_ms),
        }
        mock_get_service.return_value = mock_service

        assert is_premium_active("user-1") is True
        mock_supabase.table.return_value.update.assert_called()


def test_expired_locally_and_play_confirms_truly_expired():
    with patch("premium.supabase") as mock_supabase, \
         patch("premium._get_play_service") as mock_get_service:
        _patch_subscription_row(mock_supabase, {
            "id": "sub-1", "status": "active", "expiry_time_millis": 1000,
            "product_id": "p1", "purchase_token": "tok",
        })
        mock_service = MagicMock()
        mock_service.purchases.return_value.subscriptions.return_value.get.return_value.execute.return_value = {
            "paymentState": 0, "expiryTimeMillis": "1000",
        }
        mock_get_service.return_value = mock_service

        assert is_premium_active("user-1") is False
