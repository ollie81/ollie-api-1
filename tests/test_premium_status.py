# ============================================================
# Tests for premium.premium_status (GET /premium/status) -- the
# route itself, not is_premium_active's own logic (already covered
# by test_is_premium_active.py). is_premium_active is patched
# directly so these stay focused on what the route does with a
# known premium/non-premium result.
# ============================================================

from unittest.mock import patch, MagicMock

from premium import premium_status


def _mock_result(data):
    result = MagicMock()
    result.data = data
    return result


def test_free_user_gets_null_product_and_expiry():
    with patch("premium.is_premium_active", return_value=False), \
         patch("premium.supabase") as mock_supabase:
        result = premium_status(current_user={"id": "user-1"})

        assert result == {"is_premium": False, "product_id": None, "expiry_time_millis": None}
        mock_supabase.table.assert_not_called()


def test_active_subscription_returns_product_and_expiry():
    with patch("premium.is_premium_active", return_value=True), \
         patch("premium.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"product_id": "ollie_premium_monthly", "expiry_time_millis": 1893456000000}])

        result = premium_status(current_user={"id": "user-1"})

        assert result == {
            "is_premium": True,
            "product_id": "ollie_premium_monthly",
            "expiry_time_millis": 1893456000000,
        }


def test_lifetime_purchase_normalizes_zero_expiry_to_null():
    with patch("premium.is_premium_active", return_value=True), \
         patch("premium.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([{"product_id": "ollie_premium_lifetime", "expiry_time_millis": 0}])

        result = premium_status(current_user={"id": "user-1"})

        assert result["product_id"] == "ollie_premium_lifetime"
        assert result["expiry_time_millis"] is None


def test_premium_true_but_no_row_found_does_not_crash():
    # Defensive edge case -- shouldn't normally happen (is_premium_active
    # only returns True when it found an active row), but a mocked/stale
    # state should degrade gracefully rather than raise an IndexError.
    with patch("premium.is_premium_active", return_value=True), \
         patch("premium.supabase") as mock_supabase:
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = \
            _mock_result([])

        result = premium_status(current_user={"id": "user-1"})

        assert result == {"is_premium": True, "product_id": None, "expiry_time_millis": None}
