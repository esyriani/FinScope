"""Tests for dashboard presenter behavior."""

from finance_app.modules.dashboard.presenter import build_merchant_aggregates


def test_build_merchant_aggregates_normalizes_transaction_descriptions():
    """Verify merchant aggregates normalize transaction descriptions."""
    rows = [
        {
            "description": "AMZN MKTP CA*ABCD1234",
            "amount": 12.34,
            "category": "Shopping",
        }
    ]

    aggregates = build_merchant_aggregates(rows)

    assert "AMZN MKTP" in aggregates
    assert aggregates["AMZN MKTP"]["transaction_count"] == 1
    assert aggregates["AMZN MKTP"]["total"] == 12.34
