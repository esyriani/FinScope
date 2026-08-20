"""Read-side SQL helpers for the Home command-center page.

This module owns Home-specific database read models. It does not read Flask
request state, queue background work, or shape template presentation payloads.
"""

from typing import Any

from sqlalchemy import case, func, select

from finance_app.core.category_sql import category_label_expression, transaction_category_label_expression
from finance_app.core.constants import (
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    NON_REPORTABLE_TRANSACTION_KINDS,
    STATEMENT_IMPORT_STATUS_FAILED,
)
from finance_app.core.reporting import (
    income_amount_expression,
    income_or_tagged_transfer_credit_clause,
    reportable_transaction_clause,
    spending_impact_amount_expression,
    spending_impact_clause,
)
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import category_rules as category_rules_table
from finance_app.database.tables import merchants as merchants_table
from finance_app.database.tables import statements as statements_table
from finance_app.database.tables import transactions as transactions_table


def fetch_home_overview(conn: Any, unknown_category: Any, start_date: Any) -> Any:
    """Return current-year transaction totals for the financial pulse.

    Args:
        conn: Active SQLAlchemy Core connection.
        unknown_category: The category label treated as uncategorized.
        start_date: Inclusive date for the current calendar year.

    Returns:
        A mapping with transaction counts, year-to-date spending, income,
        unknown count, and the latest active reportable transaction date.
    """
    category_value = transaction_category_label_expression(unknown_category)
    reportable = reportable_transaction_clause()
    spending_amount = spending_impact_amount_expression()
    income_amount = income_amount_expression()
    income_clause = income_or_tagged_transfer_credit_clause()
    return (
        conn.execute(
            select(
                func.count().label("transaction_count"),
                func.coalesce(
                    func.sum(case((spending_impact_clause(), spending_amount), else_=0)),
                    0,
                ).label("ytd_spending"),
                func.coalesce(
                    func.sum(case((income_clause, income_amount), else_=0)),
                    0,
                ).label("ytd_income"),
                func.coalesce(
                    func.sum(case((category_value == unknown_category, 1), else_=0)),
                    0,
                ).label("uncategorized_count"),
                func.max(transactions_table.c.tx_date).label("latest_tx_date"),
            ).where(
                transactions_table.c.ignored == 0,
                reportable,
                transactions_table.c.tx_date >= start_date,
            )
        )
        .mappings()
        .fetchone()
    )


def fetch_attention_summary(conn: Any, unknown_category: Any) -> Any:
    """Return active ledger counts that should remain visible until resolved."""
    category_value = transaction_category_label_expression(unknown_category)
    return (
        conn.execute(
            select(
                func.coalesce(func.sum(case((category_value == unknown_category, 1), else_=0)), 0).label(
                    "unknown_count"
                ),
                func.coalesce(func.sum(case((transactions_table.c.needs_review == 1, 1), else_=0)), 0).label(
                    "needs_review_count"
                ),
            ).where(
                transactions_table.c.ignored == 0,
                transactions_table.c.transaction_kind.not_in(NON_REPORTABLE_TRANSACTION_KINDS),
            )
        )
        .mappings()
        .fetchone()
    )


def fetch_statement_count(conn: Any) -> Any:
    """Return the total number of uploaded statements."""
    return conn.execute(select(func.count()).select_from(statements_table)).scalar_one()


def fetch_latest_statement(conn: Any) -> Any:
    """Return the most recently uploaded statement with its account label."""
    return conn.execute(latest_statement_query().limit(1)).mappings().fetchone()


def fetch_recent_statements(conn: Any, limit: Any = 2) -> Any:
    """Return recent statement uploads for the activity feed."""
    return conn.execute(latest_statement_query().limit(limit)).mappings().fetchall()


def latest_statement_query() -> Any:
    """Build the shared statement query used by Home activity widgets."""
    return (
        select(
            statements_table.c.id,
            statements_table.c.filename,
            statements_table.c.uploaded_at,
            statements_table.c.import_status,
            accounts_table.c.name.label("account_name"),
        )
        .select_from(
            statements_table.outerjoin(
                accounts_table,
                accounts_table.c.id == statements_table.c.account_id,
            )
        )
        .order_by(statements_table.c.uploaded_at.desc(), statements_table.c.id.desc())
    )


def fetch_failed_imports(conn: Any, limit: Any = 3) -> Any:
    """Return failed import count and latest failed statement rows."""
    count = conn.execute(
        select(func.count())
        .select_from(statements_table)
        .where(statements_table.c.import_status == STATEMENT_IMPORT_STATUS_FAILED)
    ).scalar_one()
    rows = (
        conn.execute(
            latest_statement_query()
            .where(statements_table.c.import_status == STATEMENT_IMPORT_STATUS_FAILED)
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )
    return {
        "count": count,
        "latest": rows,
    }


def fetch_rule_suggestion_count(conn: Any) -> Any:
    """Return the number of automatic rules still awaiting approval."""
    return conn.execute(
        select(func.count())
        .select_from(category_rules_table)
        .where(
            category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC,
            category_rules_table.c.ai_approved == 0,
        )
    ).scalar_one()


def fetch_top_categories(conn: Any, unknown_category: Any, start_date: Any, limit: Any) -> Any:
    """Return top current-year spending categories for compact Home insights."""
    category = transaction_category_label_expression(unknown_category)
    total = func.sum(spending_impact_amount_expression())
    return (
        conn.execute(
            select(
                category.label("category"),
                total.label("total"),
            )
            .where(
                spending_impact_clause(),
                reportable_transaction_clause(),
                transactions_table.c.ignored == 0,
                transactions_table.c.tx_date >= start_date,
            )
            .group_by(category)
            .order_by(total.desc())
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )


def fetch_recent_reviewed_transactions(conn: Any, unknown_category: str, limit: Any = 2) -> Any:
    """Return recently reviewed transactions for the activity feed."""
    return (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transaction_category_label_expression(unknown_category).label("category"),
                transactions_table.c.reviewed_at,
            )
            .where(
                transactions_table.c.ignored == 0,
                transactions_table.c.reviewed_at.is_not(None),
            )
            .order_by(transactions_table.c.reviewed_at.desc(), transactions_table.c.id.desc())
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )


def fetch_recent_categorizations(conn: Any, unknown_category: str, limit: Any = 2) -> Any:
    """Return recent categorization events that were not already reviewed."""
    return (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transaction_category_label_expression(unknown_category).label("category"),
                transactions_table.c.category_source,
                transactions_table.c.categorized_at,
            )
            .where(
                transactions_table.c.ignored == 0,
                transactions_table.c.categorized_at.is_not(None),
                transactions_table.c.reviewed_at.is_(None),
            )
            .order_by(transactions_table.c.categorized_at.desc(), transactions_table.c.id.desc())
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )


def fetch_recent_rules(conn: Any, unknown_category: str, limit: Any = 2) -> Any:
    """Return recently created category rules for the activity feed."""
    return (
        conn.execute(
            select(
                category_rules_table.c.id,
                category_rules_table.c.keyword,
                category_label_expression(category_rules_table, unknown_category).label("category"),
                category_rules_table.c.source,
                category_rules_table.c.created_at,
                merchants_table.c.merchant_key.label("merchant_name"),
            )
            .select_from(
                category_rules_table.outerjoin(
                    merchants_table,
                    merchants_table.c.id == category_rules_table.c.merchant_id,
                )
            )
            .order_by(category_rules_table.c.created_at.desc(), category_rules_table.c.id.desc())
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )
