"""Tests for comparison insight presenter helpers."""

from finance_app.modules.comparison.presenter import build_period_insights


def public_fields(insight):
    """Return the card fields that are rendered or already part of the public context."""
    return {
        key: insight[key]
        for key in (
            "label",
            "value",
            "detail",
            "visual",
            "group",
            "tone",
            "icon",
            "title",
            "summary",
            "badge",
        )
    }


def test_build_period_insights_preserves_card_order_and_public_fields():
    """Verify the refactored presenter keeps existing comparison cards stable."""
    period_row = {
        "current": 200.0,
        "previous": 60.0,
        "change": 140.0,
        "abs_change": 140.0,
        "percent": 233.3,
        "amount_label": "+140.00 $",
        "percent_label": "+233.3%",
        "direction": "up",
        "state": "changed",
    }
    category_rows = [{**period_row, "category": "Food"}]
    merchant_rows = [{**period_row, "merchant": "METRO GROCERY", "category": "Food"}]

    insights = build_period_insights(
        category_rows,
        merchant_rows,
        {"transaction_count": 1, "spending": 200},
        {"transaction_count": 1, "spending": 60},
    )

    assert [insight["label"] for insight in insights] == [
        "Largest category increase",
        "Largest merchant increase",
        "Transaction activity",
    ]
    assert public_fields(insights[0]) == {
        "label": "Largest category increase",
        "value": "Food +140.00 $ (+233.3%)",
        "detail": "Prior: 60.00 $. Current: 200.00 $",
        "visual": "comparison",
        "group": "categories",
        "tone": "danger",
        "icon": "bi-graph-up-arrow",
        "title": "Food",
        "summary": "+140.00 $",
        "badge": "+233.3%",
    }
    assert insights[0]["previous_label"] == "60.00 $"
    assert insights[0]["current_label"] == "200.00 $"
    assert insights[0]["previous_width"] == 30.0
    assert insights[0]["current_width"] == 100.0
    assert insights[0]["insight_type"] == "category_increase"
    assert insights[0]["score"] == 140.0
    assert insights[0]["rank_reason"] == "largest absolute category increase"

    assert public_fields(insights[2]) == {
        "label": "Transaction activity",
        "value": "1 transaction",
        "detail": "0 versus prior period. Average transaction: 200.00 $",
        "visual": "activity",
        "group": "spending",
        "tone": "accent",
        "icon": "bi-activity",
        "title": "Transactions",
        "summary": "1",
        "badge": "0",
    }
    assert insights[2]["stat_items"] == [
        {"label": "Current", "value": "1"},
        {"label": "Prior", "value": "1"},
        {"label": "Average", "value": "200.00 $"},
    ]
    assert insights[2]["insight_type"] == "transaction_activity"

