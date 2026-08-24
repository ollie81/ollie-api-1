# ============================================================
# Tests for premium.activate_premium — the real purchase-
# verification route the Flutter in_app_purchase flow calls
# after a completed purchase. Covers all three product types:
# monthly/yearly subscriptions (verified via the subscriptions
# API) and lifetime (a one-time managed product, verified via
# the products API, no expiry). Mocks the supabase client and
# _get_play_service.
# ============================================================

from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

from premium import activate_premium, PLAY_LIFETIME_PRODUCT_ID

CURRENT_USER = {"id": "user-1"}


def _mock_supabase_no_existing_row(mock_supabase):
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[])


def _mock_supabase_existing_row(mock_supabase, existing_id="sub-1"):
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"id": existing_id}])


def test_missing_purchase_token_raises_400():
    with pytest.raises(HTTPException) as exc_info:
        activate_premium({"product_id": "ollie_premium_monthly"}, CURRENT_USER)
    assert exc_info.value.status_code == 400


def test_missing_product_id_raises_400():
    with pytest.raises(HTTPException) as exc_info:
        activate_premium({"purchase_token": "tok"}, CURRENT_USER)
    assert exc_info.value.status_code == 400


def test_verification_exception_raises_400():
    with patch("premium._get_play_service", side_effect=Exception("boom")):
        with pytest.raises(HTTPException) as exc_info:
            activate_premium(
                {"purchase_token": "tok", "product_id": "ollie_premium_monthly"},
                CURRENT_USER,
            )
        assert exc_info.value.status_code == 400


# ------------------------------------------------------------
# Monthly / yearly subscriptions — verified via subscriptions API
# ------------------------------------------------------------

def test_subscription_pending_not_granted():
    mock_service = MagicMock()
    mock_service.purchases.return_value.subscriptions.return_value.get.return_value.execute.return_value = {
        "paymentState": 0, "expiryTimeMillis": "9999999999999",
    }
    with patch("premium._get_play_service", return_value=mock_service):
        with pytest.raises(HTTPException) as exc_info:
            activate_premium(
                {"purchase_token": "tok", "product_id": "ollie_premium_monthly"},
                CURRENT_USER,
            )
        assert exc_info.value.status_code == 400


def test_subscription_success_inserts_new_row():
    mock_service = MagicMock()
    mock_service.purchases.return_value.subscriptions.return_value.get.return_value.execute.return_value = {
        "paymentState": 1, "expiryTimeMillis": "1999999999000",
    }
    with patch("premium._get_play_service", return_value=mock_service), \
         patch("premium.supabase") as mock_supabase:
        _mock_supabase_no_existing_row(mock_supabase)

        result = activate_premium(
            {"purchase_token": "tok", "product_id": "ollie_premium_yearly"},
            CURRENT_USER,
        )

        assert result["success"] is True
        mock_supabase.table.return_value.insert.assert_called_once()
        inserted = mock_supabase.table.return_value.insert.call_args[0][0]
        assert inserted["product_id"] == "ollie_premium_yearly"
        assert inserted["expiry_time_millis"] == 1999999999000
        assert inserted["status"] == "active"
        mock_service.purchases.return_value.subscriptions.return_value.get.assert_called_once()
        mock_service.purchases.return_value.products.assert_not_called()


def test_subscription_free_trial_granted():
    mock_service = MagicMock()
    mock_service.purchases.return_value.subscriptions.return_value.get.return_value.execute.return_value = {
        "paymentState": 2, "expiryTimeMillis": "1999999999000",
    }
    with patch("premium._get_play_service", return_value=mock_service), \
         patch("premium.supabase") as mock_supabase:
        _mock_supabase_no_existing_row(mock_supabase)

        result = activate_premium(
            {"purchase_token": "tok", "product_id": "ollie_premium_monthly"},
            CURRENT_USER,
        )
        assert result["success"] is True


def test_subscription_success_updates_existing_row():
    mock_service = MagicMock()
    mock_service.purchases.return_value.subscriptions.return_value.get.return_value.execute.return_value = {
        "paymentState": 1, "expiryTimeMillis": "1999999999000",
    }
    with patch("premium._get_play_service", return_value=mock_service), \
         patch("premium.supabase") as mock_supabase:
        _mock_supabase_existing_row(mock_supabase, existing_id="sub-42")

        activate_premium(
            {"purchase_token": "tok", "product_id": "ollie_premium_monthly"},
            CURRENT_USER,
        )

        mock_supabase.table.return_value.update.assert_called_once()
        mock_supabase.table.return_value.update.return_value.eq.assert_called_once_with("id", "sub-42")


# ------------------------------------------------------------
# Lifetime — one-time managed product, verified via products API
# ------------------------------------------------------------

def test_lifetime_pending_not_granted():
    mock_service = MagicMock()
    mock_service.purchases.return_value.products.return_value.get.return_value.execute.return_value = {
        "purchaseState": 2,
    }
    with patch("premium._get_play_service", return_value=mock_service):
        with pytest.raises(HTTPException) as exc_info:
            activate_premium(
                {"purchase_token": "tok", "product_id": PLAY_LIFETIME_PRODUCT_ID},
                CURRENT_USER,
            )
        assert exc_info.value.status_code == 400


def test_lifetime_canceled_not_granted():
    mock_service = MagicMock()
    mock_service.purchases.return_value.products.return_value.get.return_value.execute.return_value = {
        "purchaseState": 1,
    }
    with patch("premium._get_play_service", return_value=mock_service):
        with pytest.raises(HTTPException) as exc_info:
            activate_premium(
                {"purchase_token": "tok", "product_id": PLAY_LIFETIME_PRODUCT_ID},
                CURRENT_USER,
            )
        assert exc_info.value.status_code == 400


def test_lifetime_success_grants_with_zero_expiry_via_products_api():
    mock_service = MagicMock()
    mock_service.purchases.return_value.products.return_value.get.return_value.execute.return_value = {
        "purchaseState": 0,
    }
    with patch("premium._get_play_service", return_value=mock_service), \
         patch("premium.supabase") as mock_supabase:
        _mock_supabase_no_existing_row(mock_supabase)

        result = activate_premium(
            {"purchase_token": "tok", "product_id": PLAY_LIFETIME_PRODUCT_ID},
            CURRENT_USER,
        )

        assert result["success"] is True
        inserted = mock_supabase.table.return_value.insert.call_args[0][0]
        assert inserted["product_id"] == PLAY_LIFETIME_PRODUCT_ID
        assert inserted["expiry_time_millis"] == 0
        mock_service.purchases.return_value.products.return_value.get.assert_called_once()
        mock_service.purchases.return_value.subscriptions.assert_not_called()
