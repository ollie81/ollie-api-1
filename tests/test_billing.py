# ============================================================
# Tests for billing.py — the Lemon Squeezy checkout/webhook routes
# the web client uses to buy premium (the Android app's equivalent
# is premium.activate_premium, verified against Google Play
# instead). Mocks `requests` and the supabase client; computes real
# HMAC signatures against a fake test secret rather than mocking the
# verification itself, so a regression in the signing logic would
# actually fail these tests.
# ============================================================

import asyncio
import hashlib
import hmac
import json
from contextlib import ExitStack
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

import billing
from billing import create_checkout_session, lemonsqueezy_webhook

CURRENT_USER = {"id": "user-1"}
TEST_SECRET = "test-signing-secret"

_CONFIGURED = {
    "billing.LEMONSQUEEZY_API_KEY": "ls_test_fake",
    "billing.LEMONSQUEEZY_STORE_ID": "12345",
    "billing.LEMONSQUEEZY_VARIANT_MONTHLY": "111",
    "billing.LEMONSQUEEZY_VARIANT_YEARLY": "222",
    "billing.LEMONSQUEEZY_WEBHOOK_SECRET": TEST_SECRET,
}


def _patch_config():
    stack = ExitStack()
    for target, value in _CONFIGURED.items():
        stack.enter_context(patch(target, value))
    return stack


def _fake_response(status_code=200, json_body=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body or {}
    return response


def _sign(body_bytes: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()


def _fake_webhook_request(payload: dict, secret: str = TEST_SECRET, bad_signature=False):
    body_bytes = json.dumps(payload).encode()
    signature = "wrong" if bad_signature else _sign(body_bytes, secret)

    request = MagicMock()

    async def _body():
        return body_bytes

    async def _json():
        return payload

    request.body = _body
    request.json = _json
    request.headers = {"x-signature": signature}
    return request


def _run_webhook(request):
    # No pytest-asyncio in this codebase (every other route here is
    # sync) -- lemonsqueezy_webhook is the one async route (it awaits
    # request.body()/request.json()), so it's driven directly rather
    # than pulling in a new test dependency for just this one handler.
    return asyncio.run(lemonsqueezy_webhook(request))


def _subscription_payload(event_name, status, custom_data=None, **attrs):
    return {
        "meta": {
            "event_name": event_name,
            "custom_data": custom_data if custom_data is not None else {"user_id": "user-1", "plan": "monthly"},
        },
        "data": {
            "id": "sub_123",
            "type": "subscriptions",
            "attributes": {"status": status, **attrs},
        },
    }


# ---- /create-checkout-session ----

def test_missing_plan_raises_400():
    with _patch_config():
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({}, CURRENT_USER)
        assert exc_info.value.status_code == 400


def test_unknown_plan_raises_400():
    with _patch_config():
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({"plan": "lifetime"}, CURRENT_USER)
        assert exc_info.value.status_code == 400


def test_not_configured_raises_500():
    with patch("billing.LEMONSQUEEZY_API_KEY", None):
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({"plan": "monthly"}, CURRENT_USER)
        assert exc_info.value.status_code == 500


def test_valid_plan_creates_a_checkout_scoped_to_the_current_user():
    fake_response = _fake_response(201, {"data": {"attributes": {"url": "https://ollie.lemonsqueezy.com/checkout/abc"}}})
    with _patch_config(), patch("billing.requests.post", return_value=fake_response) as mock_post:
        result = create_checkout_session({"plan": "monthly"}, CURRENT_USER)

        assert result == {"checkout_url": "https://ollie.lemonsqueezy.com/checkout/abc"}
        kwargs = mock_post.call_args.kwargs
        body = kwargs["json"]["data"]
        assert body["attributes"]["checkout_data"]["custom"] == {"user_id": "user-1", "plan": "monthly"}
        assert body["relationships"]["variant"]["data"]["id"] == "111"
        assert body["relationships"]["store"]["data"]["id"] == "12345"
        assert kwargs["headers"]["Authorization"] == "Bearer ls_test_fake"


def test_lemonsqueezy_rejecting_the_request_raises_502():
    with _patch_config(), patch("billing.requests.post", return_value=_fake_response(422, {"errors": []})):
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({"plan": "yearly"}, CURRENT_USER)
        assert exc_info.value.status_code == 502


def test_network_error_creating_checkout_raises_502():
    with _patch_config(), patch("billing.requests.post", side_effect=Exception("boom")):
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({"plan": "monthly"}, CURRENT_USER)
        assert exc_info.value.status_code == 502


# ---- /webhook ----

def test_missing_signature_is_rejected():
    with _patch_config():
        request = _fake_webhook_request(_subscription_payload("subscription_created", "active"))
        request.headers = {}
        with pytest.raises(HTTPException) as exc_info:
            _run_webhook(request)
        assert exc_info.value.status_code == 400


def test_wrong_signature_is_rejected():
    with _patch_config():
        request = _fake_webhook_request(_subscription_payload("subscription_created", "active"), bad_signature=True)
        with pytest.raises(HTTPException) as exc_info:
            _run_webhook(request)
        assert exc_info.value.status_code == 400


def test_signature_computed_with_the_wrong_secret_is_rejected():
    with _patch_config():
        request = _fake_webhook_request(_subscription_payload("subscription_created", "active"), secret="someone-elses-secret")
        with pytest.raises(HTTPException) as exc_info:
            _run_webhook(request)
        assert exc_info.value.status_code == 400


def test_not_configured_rejects_everything():
    with patch("billing.LEMONSQUEEZY_WEBHOOK_SECRET", None):
        request = _fake_webhook_request(_subscription_payload("subscription_created", "active"))
        with pytest.raises(HTTPException) as exc_info:
            _run_webhook(request)
        assert exc_info.value.status_code == 400


def test_non_subscription_event_is_ignored():
    with _patch_config(), patch("billing.supabase") as mock_supabase:
        payload = {"meta": {"event_name": "order_created", "custom_data": {"user_id": "user-1"}}, "data": {}}
        result = _run_webhook(_fake_webhook_request(payload))
        assert result == {"received": True}
        mock_supabase.table.assert_not_called()


def test_missing_custom_data_is_ignored():
    with _patch_config(), patch("billing.supabase") as mock_supabase:
        payload = _subscription_payload("subscription_created", "active", custom_data={})
        result = _run_webhook(_fake_webhook_request(payload))
        assert result == {"received": True}
        mock_supabase.table.assert_not_called()


def test_active_subscription_activates_premium_using_renews_at():
    payload = _subscription_payload(
        "subscription_created", "active", renews_at="2026-10-19T08:00:00.000000Z",
    )
    with _patch_config(), patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[])

        result = _run_webhook(_fake_webhook_request(payload))

        assert result == {"received": True}
        inserted = mock_supabase.table.return_value.insert.call_args[0][0]
        assert inserted["user_id"] == "user-1"
        assert inserted["source"] == "lemonsqueezy"
        assert inserted["status"] == "active"
        assert inserted["purchase_token"] == "sub_123"
        assert inserted["product_id"] == "ollie_premium_monthly_web"
        assert inserted["expiry_time_millis"] > 0


def test_recurring_payment_success_updates_the_existing_row():
    payload = _subscription_payload(
        "subscription_payment_success", "active", renews_at="2026-11-19T08:00:00.000000Z",
    )
    with _patch_config(), patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[{"id": "sub-row-1"}])

        _run_webhook(_fake_webhook_request(payload))

        mock_supabase.table.return_value.update.assert_called_once()
        mock_supabase.table.return_value.update.return_value.eq.assert_called_once_with("id", "sub-row-1")


def test_cancelled_subscription_stays_active_until_ends_at():
    # Cancelling stops future renewal -- it doesn't revoke access the
    # user already paid for.
    payload = _subscription_payload(
        "subscription_cancelled", "cancelled",
        renews_at=None, ends_at="2026-12-01T00:00:00.000000Z",
    )
    with _patch_config(), patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[])

        _run_webhook(_fake_webhook_request(payload))

        inserted = mock_supabase.table.return_value.insert.call_args[0][0]
        assert inserted["status"] == "active"
        assert inserted["expiry_time_millis"] > 0


def test_expired_subscription_deactivates_premium():
    payload = _subscription_payload("subscription_expired", "expired")
    with _patch_config(), patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[])

        _run_webhook(_fake_webhook_request(payload))

        inserted = mock_supabase.table.return_value.insert.call_args[0][0]
        assert inserted["status"] == "expired"
        assert inserted["expiry_time_millis"] == 0


def test_unpaid_subscription_deactivates_premium():
    payload = _subscription_payload("subscription_updated", "unpaid")
    with _patch_config(), patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[])

        _run_webhook(_fake_webhook_request(payload))

        inserted = mock_supabase.table.return_value.insert.call_args[0][0]
        assert inserted["status"] == "expired"
