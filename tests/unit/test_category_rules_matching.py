"""Tests for category rule matching helpers."""

from finance_app.modules.categories.rules_matching import merchant_category_cache_key


def test_merchant_category_cache_key_includes_signed_amount():
    """Verify category cache keys only collapse exact signed amount matches."""
    assert merchant_category_cache_key("METRO", 12.345) == ("METRO", "12.35")
    assert merchant_category_cache_key("METRO", 12.34) != merchant_category_cache_key(
        "METRO",
        30.00,
    )
    assert merchant_category_cache_key("METRO", 12.34) != merchant_category_cache_key(
        "METRO",
        -12.34,
    )
    assert merchant_category_cache_key("METRO", 12.34, merchant_id=7) == ("merchant:7", "12.34")
