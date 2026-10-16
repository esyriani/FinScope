"""Tests for transaction query filters and their shared review-status policy."""

import pytest
from werkzeug.datastructures import MultiDict

from finance_app.modules.transactions.filters import (
    build_transaction_core_filters,
    parse_transaction_filters,
    transaction_sort,
)
from finance_app.modules.transactions.status import transaction_review_status


@pytest.mark.parametrize("category", [None, "", "  ", "UNKNOWN", "Custom unknown"])
def test_missing_or_unknown_category_requires_review_without_a_persisted_flag(category):
    """Unresolved categories cannot become Automatic merely because the review flag is clear."""
    row = {"category": category, "category_source": "ai", "needs_review": 0}

    assert transaction_review_status(row, "Custom unknown") == "needs_review"


def test_parse_transaction_filters_normalizes_request_args():
    """Verify that transaction filter parsing keeps only supported values."""
    args = MultiDict(
        [
            ("categories", "Groceries"),
            ("categories", ""),
            ("categories", "Utilities"),
            ("tags", "Tax"),
            ("tags", ""),
            ("filter_mode", "exclude"),
            ("category_status", "categorized"),
            ("category_source", "ai"),
            ("amount_type", "income"),
            ("ignored", "ignored"),
            ("review", "automatic"),
            ("period", "all"),
            ("sort", "amount"),
            ("direction", "asc"),
            ("page", "2"),
            ("date_from", "2026-01-01"),
            ("date_to", "not-a-date"),
            ("merchant_key", "AMZN MKTP CA*1234"),
            ("account_id", "7"),
        ]
    )

    filters = parse_transaction_filters(args, None)

    assert filters["selected_categories"] == ["Groceries", "Utilities"]
    assert filters["category"] == ""
    assert filters["selected_tags"] == ["Tax"]
    assert filters["filter_mode"] == "exclude"
    assert filters["category_status"] == "categorized"
    assert filters["category_source"] == "ai"
    assert filters["amount_type"] == "income"
    assert filters["ignored"] == "ignored"
    assert filters["review"] == "automatic"
    assert filters["period"] == "all"
    assert filters["sort"] == "amount"
    assert filters["direction"] == "asc"
    assert filters["page"] == 2
    assert filters["date_from"] == ""
    assert filters["date_to"] == ""
    assert filters["merchant_key"] == "AMZN MKTP"
    assert filters["account_id"] == 7


def test_parse_transaction_filters_keeps_custom_range_dates():
    """Verify custom range dates are normalized only for the custom period."""
    filters = parse_transaction_filters(
        MultiDict(
            [
                ("period", "custom"),
                ("date_from", "2026-02-28"),
                ("date_to", "2026-01-01"),
            ]
        ),
        None,
    )

    assert filters["period"] == "custom"
    assert filters["date_from"] == "2026-01-01"
    assert filters["date_to"] == "2026-02-28"


def test_parse_transaction_filters_defaults_invalid_values():
    """Verify that unsupported filter values fall back to safe defaults."""
    filters = parse_transaction_filters(
        MultiDict(
            [
                ("filter_mode", "bad"),
                ("category_status", "bad"),
                ("category_source", "bad"),
                ("amount_type", "bad"),
                ("ignored", "bad"),
                ("review", "bad"),
                ("period", "bad"),
                ("direction", "bad"),
                ("page", "-1"),
            ]
        ),
        None,
    )

    assert filters["filter_mode"] == "include"
    assert filters["category_status"] == ""
    assert filters["category_source"] == ""
    assert filters["amount_type"] == ""
    assert filters["ignored"] == "active"
    assert filters["review"] == ""
    assert filters["period"] == "ytd"
    assert filters["direction"] == "desc"
    assert filters["page"] == 1


def test_parse_transaction_filters_accepts_history_source():
    """Verify historical categorization is a supported source filter."""
    filters = parse_transaction_filters(
        MultiDict([("category_source", "history")]),
        None,
    )

    assert filters["category_source"] == "history"


def test_build_transaction_core_filters_combines_high_value_filters():
    """Verify that parsed transaction filters translate into Core criteria."""
    filters = parse_transaction_filters(
        MultiDict(
            [
                ("search", "metro"),
                ("categories", "Groceries"),
                ("tags", "Tax"),
                ("filter_mode", "exclude"),
                ("category_status", "categorized"),
                ("category_source", "manual_reviewed"),
                ("amount_type", "spending"),
                ("ignored", "active"),
                ("period", "custom"),
                ("date_from", "2026-01-01"),
                ("date_to", "2026-01-31"),
                ("account_id", "7"),
            ]
        ),
        None,
    )

    core_filters = build_transaction_core_filters(filters, "UNKNOWN")
    sql = "\n".join(str(condition) for condition in core_filters.criteria())

    assert "transactions.category_id" in sql
    assert "transactions.category" in sql
    assert "NOT IN" in sql
    assert "NOT (EXISTS" in sql
    assert "transaction_tags" in sql
    assert "tags.name IN" in sql
    assert "transactions.needs_review" in sql
    assert "transactions.reviewed_at IS NOT NULL" in sql
    assert "transactions.amount >" in sql
    assert "transactions.ignored" in sql
    assert "transactions.tx_date >=" in sql
    assert "transactions.tx_date <=" in sql
    assert "transactions.account_id" in sql


def test_build_transaction_core_filters_supports_credit_filter():
    """Verify dashboard credit drill-down can include income and transfer credits."""
    filters = parse_transaction_filters(
        MultiDict([("amount_type", "credit")]),
        None,
    )

    core_filters = build_transaction_core_filters(filters, "UNKNOWN")
    sql = "\n".join(str(condition) for condition in core_filters.criteria())

    assert filters["amount_type"] == "credit"
    assert "transactions.amount <" in sql
    assert "transactions.transaction_kind IN" in sql


def test_transaction_sort_restricts_sort_expression():
    """Verify that transaction sorting only resolves allowed sort keys."""
    filters = {"sort": "category"}

    sort, expression = transaction_sort(filters, "Owner's Draw")
    assert sort == "category"
    assert "coalesce" in str(expression).lower()

    filters["sort"] = "unsafe"
    sort, expression = transaction_sort(filters, "UNKNOWN")
    assert sort == "date"
    assert str(expression) == "transactions.tx_date"
