"""Tests for transaction query filter parsing."""

from werkzeug.datastructures import MultiDict

from finance_app.modules.transactions.filters import (
    build_transaction_core_filters,
    parse_transaction_filters,
    transaction_sort,
)


def test_parse_transaction_filters_normalizes_request_args(db_conn):
    """Verify that transaction filter parsing keeps only supported values."""
    args = MultiDict(
        [
            ("categories", "Groceries"),
            ("category", "Utilities"),
            ("categories", ""),
            ("tags", "Tax"),
            ("tags", ""),
            ("filter_mode", "exclude"),
            ("category_status", "categorized"),
            ("category_source", "ai"),
            ("amount_type", "income"),
            ("ignored", "ignored"),
            ("review", "pending_approval"),
            ("period", "all"),
            ("sort", "amount"),
            ("direction", "asc"),
            ("page", "2"),
            ("date_from", "2026-01-01"),
            ("date_to", "not-a-date"),
            ("merchant_key", "AMZN MKTP CA*1234"),
        ]
    )

    filters = parse_transaction_filters(args, db_conn)

    assert filters["selected_categories"] == ["Groceries", "Utilities"]
    assert filters["selected_tags"] == ["Tax"]
    assert filters["filter_mode"] == "exclude"
    assert filters["category_status"] == "categorized"
    assert filters["category_source"] == "ai"
    assert filters["amount_type"] == "income"
    assert filters["ignored"] == "ignored"
    assert filters["review"] == "pending_approval"
    assert filters["period"] == "all"
    assert filters["sort"] == "amount"
    assert filters["direction"] == "asc"
    assert filters["page"] == 2
    assert filters["date_from"] == "2026-01-01"
    assert filters["date_to"] == ""
    assert filters["merchant_key"] == "AMZN MKTP"


def test_parse_transaction_filters_defaults_invalid_values(db_conn):
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
        db_conn,
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


def test_parse_transaction_filters_accepts_history_source(db_conn):
    """Verify historical categorization is a supported source filter."""
    filters = parse_transaction_filters(
        MultiDict([("category_source", "history")]),
        db_conn,
    )

    assert filters["category_source"] == "history"


def test_build_transaction_core_filters_combines_high_value_filters(db_conn):
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
            ]
        ),
        db_conn,
    )

    core_filters = build_transaction_core_filters(filters, "UNKNOWN")
    sql = "\n".join(str(condition) for condition in core_filters.criteria())

    assert "coalesce(transactions.category" in sql.lower()
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


def test_build_transaction_core_filters_supports_credit_filter(db_conn):
    """Verify dashboard credit drill-down can include income and transfer credits."""
    filters = parse_transaction_filters(
        MultiDict([("amount_type", "credit")]),
        db_conn,
    )

    core_filters = build_transaction_core_filters(filters, "UNKNOWN")
    sql = "\n".join(str(condition) for condition in core_filters.criteria())

    assert filters["amount_type"] == "credit"
    assert "transactions.amount <" in sql
    assert "transactions.transaction_kind IN" in sql


def test_build_transaction_core_filters_supports_pending_approval_review_filter(db_conn):
    """Verify pending approval means categorized but not manually approved."""
    filters = parse_transaction_filters(
        MultiDict([("review", "pending_approval")]),
        db_conn,
    )

    core_filters = build_transaction_core_filters(filters, "UNKNOWN")
    sql = "\n".join(str(condition) for condition in core_filters.criteria())

    assert filters["review"] == "pending_approval"
    assert "transactions.needs_review" in sql
    assert "transactions.reviewed_at IS NULL" in sql


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
