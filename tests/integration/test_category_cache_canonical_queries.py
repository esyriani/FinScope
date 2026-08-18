"""Regression tests for category_id as the canonical category identity."""

from sqlalchemy import insert
from tests.support.database import insert_transaction
from werkzeug.datastructures import MultiDict

from finance_app.core.constants import CATEGORY_RULE_SOURCE_MANUAL, TRANSACTION_KIND_EXPENSE
from finance_app.database.tables import category_rules as category_rules_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.repository import get_category_rules, resolve_category_id
from finance_app.modules.home.service import fetch_top_categories
from finance_app.modules.reports.constants import REPORT_BASIS_CASH_FLOW
from finance_app.modules.reports.queries import fetch_category_breakdown
from finance_app.modules.taxonomy_admin.service import fetch_category_usage
from finance_app.modules.transactions.filters import build_transaction_core_filters, parse_transaction_filters
from finance_app.modules.transactions.queries import count_transactions


def test_transaction_queries_use_category_id_when_cached_label_drifts(core_conn):
    """Verify reports, Home, filters, and usage counts prefer category_id."""
    food_id = resolve_category_id(core_conn, "Food")
    utilities_id = resolve_category_id(core_conn, "Utilities")
    insert_transaction(
        core_conn,
        description="Cache drift utility bill",
        amount=80.00,
        tx_date="2026-03-01",
        category="Food",
        category_id=utilities_id,
        needs_review=0,
        fingerprint="category-cache-drift-transaction",
    )

    top_categories = fetch_top_categories(core_conn, "UNKNOWN", "2026-01-01", 5)
    report_rows = fetch_category_breakdown(
        core_conn,
        [transactions_table.c.ignored == 0],
        "UNKNOWN",
        REPORT_BASIS_CASH_FLOW,
    )
    utility_filters = parse_transaction_filters(
        MultiDict([("period", "all"), ("categories", "Utilities")]),
        core_conn,
    )
    food_filters = parse_transaction_filters(
        MultiDict([("period", "all"), ("categories", "Food")]),
        core_conn,
    )

    assert [(row["category"], row["total"]) for row in top_categories] == [("Utilities", 80.00)]
    assert [(row["label"], row["spending"]) for row in report_rows] == [("Utilities", 80.00)]
    assert (
        count_transactions(
            core_conn,
            build_transaction_core_filters(utility_filters, "UNKNOWN", conn=core_conn).criteria(),
        )
        == 1
    )
    assert (
        count_transactions(
            core_conn,
            build_transaction_core_filters(food_filters, "UNKNOWN", conn=core_conn).criteria(),
        )
        == 0
    )
    assert fetch_category_usage(core_conn, food_id, "Food")["transaction_count"] == 0
    assert fetch_category_usage(core_conn, utilities_id, "Utilities")["transaction_count"] == 1


def test_rule_queries_use_category_id_when_cached_label_drifts(core_conn):
    """Verify rule behavior and usage counts prefer category_id."""
    food_id = resolve_category_id(core_conn, "Food")
    utilities_id = resolve_category_id(core_conn, "Utilities")
    result = core_conn.execute(
        insert(category_rules_table).values(
            keyword="CACHE DRIFT RULE",
            category="Food",
            category_id=utilities_id,
            source=CATEGORY_RULE_SOURCE_MANUAL,
            ai_approved=0,
        )
    )
    rule_id = result.inserted_primary_key[0]
    core_conn.commit()

    rules = get_category_rules(core_conn)
    rule = next(row for row in rules if row["id"] == rule_id)

    assert rule["category"] == "Utilities"
    assert fetch_category_usage(core_conn, food_id, "Food")["rule_count"] == 0
    assert fetch_category_usage(core_conn, utilities_id, "Utilities")["rule_count"] == 1


def test_cached_category_text_without_id_is_not_category_identity(core_conn):
    """Verify cached labels alone do not drive category filters or usage."""
    food_id = resolve_category_id(core_conn, "Food")
    core_conn.execute(
        insert(transactions_table).values(
            tx_date="2026-03-02",
            description="Unresolved cached food label",
            amount=25.00,
            category="Food",
            category_id=None,
            needs_review=0,
            transaction_kind=TRANSACTION_KIND_EXPENSE,
            fingerprint="category-cache-no-id-transaction",
        )
    )
    core_conn.commit()

    top_categories = fetch_top_categories(core_conn, "UNKNOWN", "2026-01-01", 5)
    report_rows = fetch_category_breakdown(
        core_conn,
        [transactions_table.c.ignored == 0],
        "UNKNOWN",
        REPORT_BASIS_CASH_FLOW,
    )
    food_filters = parse_transaction_filters(
        MultiDict([("period", "all"), ("categories", "Food")]),
        core_conn,
    )

    assert [(row["category"], row["total"]) for row in top_categories] == [("UNKNOWN", 25.00)]
    assert [(row["label"], row["spending"]) for row in report_rows] == [("UNKNOWN", 25.00)]
    assert (
        count_transactions(
            core_conn,
            build_transaction_core_filters(food_filters, "UNKNOWN", conn=core_conn).criteria(),
        )
        == 0
    )
    assert fetch_category_usage(core_conn, food_id, "Food")["transaction_count"] == 0
