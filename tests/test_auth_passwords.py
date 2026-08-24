# ============================================================
# Tests for auth.hash_password / auth.verify_password.
# ============================================================

from auth import hash_password, verify_password


def test_hash_then_verify_correct_password_succeeds():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_wrong_password_fails():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False


def test_verify_against_empty_hash_returns_false_not_raises():
    # Google-only accounts are created with password_hash="" -- this
    # must fail cleanly (a 401), not throw and surface as a raw 500.
    assert verify_password("anything", "") is False


def test_verify_against_malformed_hash_returns_false_not_raises():
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_hash_is_salted():
    # bcrypt salts each hash -- same password, different output --
    # but both must still verify correctly.
    first = hash_password("same password")
    second = hash_password("same password")
    assert first != second
    assert verify_password("same password", first) is True
    assert verify_password("same password", second) is True
