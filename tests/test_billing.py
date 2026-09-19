# ============================================================
# Tests for billing.py — the Stripe checkout/webhook routes the
# web client uses to buy premium (the Android app's equivalent is
# premium.activate_premium, verified against Google Play instead).
# Mocks the supabase client and the stripe SDK calls; never touches
# a real Stripe account.
# ============================================================

import asyncio
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

import billing
from billing import create_checkout_session, stripe_webhook

CURRENT_USER = {"id": "user-1"}


def _fake_request(body: bytes, signature="sig"):
    request = MagicMock()

    async def _body():
        return body

    request.body = _body
    request.headers = {"stripe-signature": signature}
    return request


def _run_webhook(request):
    # No pytest-asyncio in this codebase (every other route here is
    # sync) -- stripe_webhook is the one async route (it awaits
    # request.body()), so it's driven directly rather than pulling
    # in a new test dependency for just this one handler.
    return asyncio.run(stripe_webhook(request))


# ---- /create-checkout-session ----

def test_missing_plan_raises_400():
    with patch("billing.STRIPE_SECRET_KEY", "sk_test_fake"):
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({}, CURRENT_USER)
        assert exc_info.value.status_code == 400


def test_unknown_plan_raises_400():
    with patch("billing.STRIPE_SECRET_KEY", "sk_test_fake"):
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({"plan": "lifetime"}, CURRENT_USER)
        assert exc_info.value.status_code == 400


def test_valid_plan_creates_a_session_scoped_to_the_current_user():
    mock_session = MagicMock(url="https://checkout.stripe.com/pay/cs_test_123")
    with patch("billing.STRIPE_SECRET_KEY", "sk_test_fake"), \
         patch("billing.STRIPE_PRICE_MONTHLY", "price_monthly_fake"), \
         patch("billing.stripe.checkout.Session.create", return_value=mock_session) as mock_create:
        result = create_checkout_session({"plan": "monthly"}, CURRENT_USER)

        assert result == {"checkout_url": mock_session.url}
        kwargs = mock_create.call_args.kwargs
        assert kwargs["client_reference_id"] == "user-1"
        assert kwargs["mode"] == "subscription"
        assert kwargs["line_items"] == [{"price": "price_monthly_fake", "quantity": 1}]


def test_stripe_not_configured_raises_500():
    with patch("billing.STRIPE_SECRET_KEY", None):
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({"plan": "monthly"}, CURRENT_USER)
        assert exc_info.value.status_code == 500


def test_stripe_error_creating_session_raises_502():
    with patch("billing.STRIPE_SECRET_KEY", "sk_test_fake"), \
         patch("billing.STRIPE_PRICE_YEARLY", "price_yearly_fake"), \
         patch("billing.stripe.checkout.Session.create", side_effect=Exception("boom")):
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({"plan": "yearly"}, CURRENT_USER)
        assert exc_info.value.status_code == 502


# ---- /webhook ----

def test_invalid_signature_is_rejected():
    with patch("billing.stripe.Webhook.construct_event", side_effect=Exception("bad sig")):
        with pytest.raises(HTTPException) as exc_info:
            _run_webhook(_fake_request(b"{}"))
        assert exc_info.value.status_code == 400


def test_checkout_completed_activates_premium_for_the_referenced_user():
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"client_reference_id": "user-1", "subscription": "sub_123"}},
    }
    fake_subscription = {
        "id": "sub_123",
        "status": "active",
        # current_period_end lives on the item, not the subscription
        # itself, as of Stripe API version 2025-03-31.basil -- this
        # fixture mirrors the real shape so a regression back to
        # reading sub["current_period_end"] fails this test instead
        # of only failing silently against real Stripe webhooks.
        "items": {"data": [{"price": {"id": "price_monthly"}, "current_period_end": 1999999999}]},
    }
    with patch("billing.stripe.Webhook.construct_event", return_value=event), \
         patch("billing.stripe.Subscription.retrieve", return_value=fake_subscription), \
         patch("billing.STRIPE_PRICE_MONTHLY", "price_monthly"), \
         patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[])

        result = _run_webhook(_fake_request(b"{}"))

        assert result == {"received": True}
        inserted = mock_supabase.table.return_value.insert.call_args[0][0]
        assert inserted["user_id"] == "user-1"
        assert inserted["source"] == "stripe"
        assert inserted["purchase_token"] == "sub_123"
        assert inserted["product_id"] == "ollie_premium_monthly_web"
        assert inserted["expiry_time_millis"] == 1999999999000
        assert inserted["status"] == "active"


def test_checkout_completed_without_a_subscription_is_ignored():
    # Not every Checkout Session is for a subscription -- nothing to
    # activate without one, and no client_reference_id/subscription
    # id pair to look anything up by.
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"client_reference_id": "user-1", "subscription": None}},
    }
    with patch("billing.stripe.Webhook.construct_event", return_value=event), \
         patch("billing.stripe.Subscription.retrieve") as mock_retrieve:
        result = _run_webhook(_fake_request(b"{}"))
        assert result == {"received": True}
        mock_retrieve.assert_not_called()


def test_subscription_updated_syncs_the_existing_row_by_purchase_token():
    event = {
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_123", "status": "active",
            "items": {"data": [{"price": {"id": "price_yearly"}, "current_period_end": 1999999999}]},
        }},
    }
    with patch("billing.stripe.Webhook.construct_event", return_value=event), \
         patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.side_effect = [
            MagicMock(execute=MagicMock(return_value=MagicMock(data=[{"user_id": "user-1"}]))),
            MagicMock(execute=MagicMock(return_value=MagicMock(data=[{"id": "sub-row-1"}]))),
        ]

        result = _run_webhook(_fake_request(b"{}"))

        assert result == {"received": True}
        mock_supabase.table.return_value.update.assert_called_once()
        updated = mock_supabase.table.return_value.update.call_args[0][0]
        assert updated["status"] == "active"
        assert updated["source"] == "stripe"


def test_subscription_deleted_marks_the_row_expired():
    event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {
            "id": "sub_123", "status": "canceled",
            "items": {"data": [{"price": {"id": "price_yearly"}, "current_period_end": 1999999999}]},
        }},
    }
    with patch("billing.stripe.Webhook.construct_event", return_value=event), \
         patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.side_effect = [
            MagicMock(execute=MagicMock(return_value=MagicMock(data=[{"user_id": "user-1"}]))),
            MagicMock(execute=MagicMock(return_value=MagicMock(data=[{"id": "sub-row-1"}]))),
        ]

        _run_webhook(_fake_request(b"{}"))

        updated = mock_supabase.table.return_value.update.call_args[0][0]
        assert updated["status"] == "expired"


def test_subscription_event_for_unknown_subscription_is_ignored():
    event = {
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_unknown", "status": "active",
            "items": {"data": [{"price": {"id": "price_yearly"}, "current_period_end": 1999999999}]},
        }},
    }
    with patch("billing.stripe.Webhook.construct_event", return_value=event), \
         patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[])

        result = _run_webhook(_fake_request(b"{}"))

        assert result == {"received": True}
        mock_supabase.table.return_value.update.assert_not_called()
        mock_supabase.table.return_value.insert.assert_not_called()
