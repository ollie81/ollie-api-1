# ============================================================
# Tests for billing.py — the Flutterwave checkout/webhook routes
# the web client uses to buy premium (the Android app's equivalent
# is premium.activate_premium, verified against Google Play
# instead). Mocks `requests` and the supabase client; never makes a
# real network call.
# ============================================================

import asyncio
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

import billing
from billing import create_checkout_session, flutterwave_webhook

CURRENT_USER = {"id": "user-1", "email": "user1@example.com"}


def _fake_response(status_code=200, json_body=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body or {}
    return response


def _fake_request(body: dict, verif_hash="secret-hash"):
    request = MagicMock()

    async def _json():
        return body

    request.json = _json
    request.headers = {"verif-hash": verif_hash}
    return request


def _run_webhook(request):
    # No pytest-asyncio in this codebase (every other route here is
    # sync) -- flutterwave_webhook is the one async route (it awaits
    # request.json()), so it's driven directly rather than pulling in
    # a new test dependency for just this one handler.
    return asyncio.run(flutterwave_webhook(request))


_CONFIGURED = {
    "billing.FLUTTERWAVE_SECRET_KEY": "flw_test_fake",
    "billing.FLUTTERWAVE_PLAN_MONTHLY": "111",
    "billing.FLUTTERWAVE_PLAN_YEARLY": "222",
    "billing.FLUTTERWAVE_PRICE_MONTHLY": "4.99",
    "billing.FLUTTERWAVE_PRICE_YEARLY": "39.99",
    "billing.FLUTTERWAVE_CURRENCY": "USD",
    "billing.FLUTTERWAVE_WEBHOOK_SECRET_HASH": "secret-hash",
}


def _patch_config():
    # A single context manager patching every config value the module
    # reads by name, rather than one `with` per value.
    from contextlib import ExitStack

    stack = ExitStack()
    for target, value in _CONFIGURED.items():
        stack.enter_context(patch(target, value))
    return stack


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


def test_flutterwave_not_configured_raises_500():
    with patch("billing.FLUTTERWAVE_SECRET_KEY", None):
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({"plan": "monthly"}, CURRENT_USER)
        assert exc_info.value.status_code == 500


def test_valid_plan_creates_a_checkout_scoped_to_the_current_user():
    fake_response = _fake_response(200, {
        "status": "success",
        "data": {"link": "https://checkout.flutterwave.com/v3/hosted/pay/abc123"},
    })
    with _patch_config(), patch("billing.requests.post", return_value=fake_response) as mock_post:
        result = create_checkout_session({"plan": "monthly"}, CURRENT_USER)

        assert result == {"checkout_url": "https://checkout.flutterwave.com/v3/hosted/pay/abc123"}
        kwargs = mock_post.call_args.kwargs
        assert kwargs["json"]["payment_plan"] == "111"
        assert kwargs["json"]["amount"] == "4.99"
        assert kwargs["json"]["currency"] == "USD"
        tx_ref = kwargs["json"]["tx_ref"]
        assert tx_ref.startswith("ollie_monthly_user-1_")
        assert kwargs["headers"]["Authorization"] == "Bearer flw_test_fake"


def test_flutterwave_rejecting_the_request_raises_502():
    fake_response = _fake_response(400, {"status": "error", "message": "invalid plan"})
    with _patch_config(), patch("billing.requests.post", return_value=fake_response):
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({"plan": "yearly"}, CURRENT_USER)
        assert exc_info.value.status_code == 502


def test_network_error_creating_checkout_raises_502():
    with _patch_config(), patch("billing.requests.post", side_effect=Exception("boom")):
        with pytest.raises(HTTPException) as exc_info:
            create_checkout_session({"plan": "monthly"}, CURRENT_USER)
        assert exc_info.value.status_code == 502


# ---- /webhook ----

def test_wrong_verif_hash_is_rejected():
    with _patch_config():
        with pytest.raises(HTTPException) as exc_info:
            _run_webhook(_fake_request({"event": "charge.completed"}, verif_hash="wrong"))
        assert exc_info.value.status_code == 400


def test_missing_verif_hash_is_rejected():
    with _patch_config():
        with pytest.raises(HTTPException) as exc_info:
            _run_webhook(_fake_request({"event": "charge.completed"}, verif_hash=""))
        assert exc_info.value.status_code == 400


def test_non_charge_event_is_ignored_without_verifying():
    with _patch_config(), patch("billing.requests.get") as mock_get:
        result = _run_webhook(_fake_request({"event": "subscription.cancelled", "data": {"id": 1}}))
        assert result == {"received": True}
        mock_get.assert_not_called()


def test_charge_completed_activates_premium_after_independent_verification():
    verify_response = _fake_response(200, {
        "status": "success",
        "data": {
            "status": "successful",
            "tx_ref": "ollie_monthly_user-1_abcdef",
            "amount": 4.99,
            "currency": "USD",
        },
    })
    with _patch_config(), \
         patch("billing.requests.get", return_value=verify_response) as mock_get, \
         patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[])

        result = _run_webhook(_fake_request({"event": "charge.completed", "data": {"id": 999}}))

        assert result == {"received": True}
        # The webhook body's own fields are never trusted directly --
        # activation only happens off the re-fetched verify response.
        mock_get.assert_called_once()
        assert "999" in mock_get.call_args.args[0]
        inserted = mock_supabase.table.return_value.insert.call_args[0][0]
        assert inserted["user_id"] == "user-1"
        assert inserted["source"] == "flutterwave"
        assert inserted["product_id"] == "ollie_premium_monthly_web"
        assert inserted["status"] == "active"


def test_charge_completed_updates_an_existing_row():
    verify_response = _fake_response(200, {
        "status": "success",
        "data": {
            "status": "successful",
            "tx_ref": "ollie_yearly_user-1_abcdef",
            "amount": 39.99,
            "currency": "USD",
        },
    })
    with _patch_config(), \
         patch("billing.requests.get", return_value=verify_response), \
         patch("billing.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[{"id": "sub-row-1"}])

        _run_webhook(_fake_request({"event": "charge.completed", "data": {"id": 1000}}))

        mock_supabase.table.return_value.update.assert_called_once()
        mock_supabase.table.return_value.update.return_value.eq.assert_called_once_with("id", "sub-row-1")


def test_verification_failure_does_not_activate_anything():
    with _patch_config(), \
         patch("billing.requests.get", return_value=_fake_response(200, {"status": "error"})), \
         patch("billing.supabase") as mock_supabase:
        _run_webhook(_fake_request({"event": "charge.completed", "data": {"id": 1}}))
        mock_supabase.table.return_value.insert.assert_not_called()
        mock_supabase.table.return_value.update.assert_not_called()


def test_verified_but_not_successful_status_does_not_activate():
    verify_response = _fake_response(200, {
        "status": "success",
        "data": {"status": "pending", "tx_ref": "ollie_monthly_user-1_x", "amount": 4.99, "currency": "USD"},
    })
    with _patch_config(), \
         patch("billing.requests.get", return_value=verify_response), \
         patch("billing.supabase") as mock_supabase:
        _run_webhook(_fake_request({"event": "charge.completed", "data": {"id": 1}}))
        mock_supabase.table.return_value.insert.assert_not_called()


def test_amount_below_expected_price_does_not_activate():
    # A forged/replayed webhook pointing at a cheaper genuine
    # transaction id shouldn't be able to grant premium for less
    # than what the plan actually costs.
    verify_response = _fake_response(200, {
        "status": "success",
        "data": {"status": "successful", "tx_ref": "ollie_monthly_user-1_x", "amount": 0.50, "currency": "USD"},
    })
    with _patch_config(), \
         patch("billing.requests.get", return_value=verify_response), \
         patch("billing.supabase") as mock_supabase:
        _run_webhook(_fake_request({"event": "charge.completed", "data": {"id": 1}}))
        mock_supabase.table.return_value.insert.assert_not_called()


def test_currency_mismatch_does_not_activate():
    verify_response = _fake_response(200, {
        "status": "success",
        "data": {"status": "successful", "tx_ref": "ollie_monthly_user-1_x", "amount": 4.99, "currency": "RWF"},
    })
    with _patch_config(), \
         patch("billing.requests.get", return_value=verify_response), \
         patch("billing.supabase") as mock_supabase:
        _run_webhook(_fake_request({"event": "charge.completed", "data": {"id": 1}}))
        mock_supabase.table.return_value.insert.assert_not_called()


def test_malformed_tx_ref_does_not_activate():
    verify_response = _fake_response(200, {
        "status": "success",
        "data": {"status": "successful", "tx_ref": "not-ours-at-all", "amount": 4.99, "currency": "USD"},
    })
    with _patch_config(), \
         patch("billing.requests.get", return_value=verify_response), \
         patch("billing.supabase") as mock_supabase:
        _run_webhook(_fake_request({"event": "charge.completed", "data": {"id": 1}}))
        mock_supabase.table.return_value.insert.assert_not_called()
