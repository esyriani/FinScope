"""SQLAlchemy query-shape regression tests.

These tests protect performance-sensitive filters and aggregations from
regressing back to broad database reads followed by Python-only filtering.
"""

from contextlib import contextmanager

from sqlalchemy import event, insert, select
from werkzeug.datastructures import MultiDict

from finance_app.database import engine as engine_module
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.calendar.queries import fetch_month_transactions
from finance_app.modules.comparison.queries import fetch_period_summary
from finance_app.modules.dashboard.queries import fetch_spending_by_category
from finance_app.modules.review.queries import review_candidate_rows
from finance_app.modules.rules.engine import rule_sql_candidate_condition
from finance_app.modules.transactions.filters import (
    build_transaction_core_filters,
    parse_transaction_filters,
)


@contextmanager
def captured_sql():
    """Capture SQL emitted through the current application engine."""
    statements = []
    engine = engine_module.get_database_engine()

    def collect(conn, cursor, statement, parameters, context, executemany):
        """Record one SQL statement emitted by SQLAlchemy."""
        del conn, cursor, parameters, context, executemany
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", collect)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", collect)


def compiled_sql(statement):
    """Compile a Core statement with the active database dialect."""
    return str(statement.compile(dialect=engine_module.get_database_engine().dialect))


def test_transaction_merchant_filter_builds_sql_candidate_predicate(core_conn):
    """Verify merchant filters include SQL predicates before Python normalization."""
    core_conn.execute(
        insert(transactions_table),
        [
            {
                "tx_date": "2026-01-01",
                "description": "AMZN MKTP CA*1234",
                "amount": 20,
                "category": "Food",
                "category_id": resolve_category_id(core_conn, "Food"),
                "fingerprint": "query-shape-amazon",
            },
            {
                "tx_date": "2026-01-02",
                "description": "Local Market",
                "amount": 10,
                "category": "Food",
                "category_id": resolve_category_id(core_conn, "Food"),
                "fingerprint": "query-shape-local",
            },
        ],
    )
    filters = parse_transaction_filters(
        MultiDict([("period", "all"), ("merchant_key", "AMZN MKTP")]),
        core_conn,
    )

    with captured_sql() as statements:
        core_filters = build_transaction_core_filters(filters, "UNKNOWN", conn=core_conn)

    candidate_sql = "\n".join(statement.lower() for statement in statements)
    sql = compiled_sql(select(transactions_table.c.id).where(*core_filters.criteria())).lower()

    assert "upper(transactions.description) like" in candidate_sql
    assert "transactions.id in" in sql
    assert "transactions.ignored" in sql


def test_rule_candidate_filter_builds_sql_amount_and_description_predicates(core_conn):
    """Verify rule previews narrow candidates with SQL amount and text predicates."""
    rule = {
        "id": 1,
        "merchant_id": None,
        "keyword": "METRO",
        "category": "Food",
        "amount_min": 10,
        "amount_max": 20,
    }

    condition = rule_sql_candidate_condition(core_conn, rule, "METRO")
    sql = compiled_sql(select(transactions_table.c.id).where(condition)).lower()

    assert "transactions.ignored" in sql
    assert "upper(transactions.description) like" in sql
    assert "transactions.amount >=" in sql
    assert "transactions.amount <=" in sql


def test_review_merchant_filter_executes_sql_candidate_predicate(core_conn):
    """Verify review merchant searches are pushed into the SQL query."""
    with captured_sql() as statements:
        review_candidate_rows(core_conn, "UNKNOWN", merchant_candidate="hydro")

    review_sql = "\n".join(statement.lower() for statement in statements if "from transactions" in statement.lower())
    assert "transactions.ignored" in review_sql
    assert "upper(transactions.description) like" in review_sql


def test_dashboard_category_aggregation_uses_sql_grouping(core_conn):
    """Verify category analytics are aggregated with SQL grouping."""
    with captured_sql() as statements:
        fetch_spending_by_category(core_conn, [transactions_table.c.ignored == 0], "UNKNOWN")

    dashboard_sql = "\n".join(statement.lower() for statement in statements if "from transactions" in statement.lower())
    assert "sum(" in dashboard_sql
    assert "group by" in dashboard_sql


def test_comparison_period_summary_aggregates_with_sql_date_filters(core_conn):
    """Verify comparison period totals are grouped by SQL aggregate predicates."""
    with captured_sql() as statements:
        fetch_period_summary(
            core_conn,
            "2026-01-01",
            "2026-01-31",
            (),
            "UNKNOWN",
        )

    comparison_sql = "\n".join(
        statement.lower() for statement in statements if "from transactions" in statement.lower()
    )
    assert "sum(" in comparison_sql
    assert "case" in comparison_sql
    assert "transactions.tx_date >=" in comparison_sql
    assert "transactions.tx_date <=" in comparison_sql
    assert "transactions.ignored" in comparison_sql


def test_calendar_month_transactions_apply_sql_filters_before_presenting_rows(core_conn):
    """Verify calendar month reads push date/category filters into SQL."""
    with captured_sql() as statements:
        fetch_month_transactions(
            core_conn,
            "2026-02-01",
            "2026-02-28",
            "UNKNOWN",
            (transactions_table.c.category == "Food",),
        )

    calendar_sql = "\n".join(statement.lower() for statement in statements if "from transactions" in statement.lower())
    assert "transactions.tx_date >=" in calendar_sql
    assert "transactions.tx_date <=" in calendar_sql
    assert "transactions.category =" in calendar_sql
    assert "transactions.transaction_kind not in" in calendar_sql
