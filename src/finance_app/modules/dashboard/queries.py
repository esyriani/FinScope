"""SQLAlchemy Core query helpers for the dashboard feature."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import case, exists, func, select

from finance_app.core.reporting import (
    income_amount_expression,
    income_or_tagged_transfer_credit_clause,
    reportable_or_tagged_transfer_credit_clause,
    reportable_transaction_clause,
    spending_impact_amount_expression,
    spending_impact_clause,
)
from finance_app.database.dates import date_month_identity
from finance_app.database.tables import (
    transaction_tags as transaction_tags_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_MANUAL,
    CATEGORY_SOURCE_RULE,
)


def non_transfer_clause() -> Any:
    """Return the dashboard Core filter for reportable transaction kinds."""
    return reportable_transaction_clause()


def fetch_summary(
    conn: Any,
    filters: Sequence[Any],
    unknown_category: str,
    include_transfer_credits: bool = False,
) -> Any:
    """Fetch dashboard summary totals, optionally including filtered transfer credits."""
    category = func.coalesce(transactions_table.c.category, unknown_category)
    has_tag = exists(select(1).where(transaction_tags_table.c.transaction_id == transactions_table.c.id))
    categorized = category != unknown_category
    untagged_spending = spending_impact_clause() & non_transfer_clause() & ~has_tag
    spending_amount = spending_impact_amount_expression()
    return (
        conn.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                spending_impact_clause() & non_transfer_clause(),
                                spending_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("total_spending"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (transactions_table.c.amount < 0)
                                & income_or_tagged_transfer_credit_clause(include_transfer_credits)
                                & reportable_or_tagged_transfer_credit_clause(include_transfer_credits),
                                income_amount_expression(),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("total_income"),
                func.count().label("transaction_count"),
                func.coalesce(func.avg(func.abs(transactions_table.c.amount)), 0).label("average_transaction_amount"),
                func.coalesce(func.sum(case((category == unknown_category, 1), else_=0)), 0).label(
                    "uncategorized_count"
                ),
                func.coalesce(func.sum(case((untagged_spending, 1), else_=0)), 0).label("untagged_spending_count"),
                func.coalesce(
                    func.sum(case((untagged_spending, spending_amount), else_=0)),
                    0,
                ).label("untagged_spending_total"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (transactions_table.c.needs_review == 1) & (category == unknown_category),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("unknown_needs_review_count"),
                func.coalesce(func.sum(case((categorized, 1), else_=0)), 0).label("categorized_count"),
                func.coalesce(
                    func.sum(case((transactions_table.c.needs_review == 1, 1), else_=0)),
                    0,
                ).label("needs_review_count"),
                func.coalesce(
                    func.sum(case((transactions_table.c.reviewed_at.is_not(None), 1), else_=0)),
                    0,
                ).label("manually_reviewed_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                categorized & (transactions_table.c.category_source == CATEGORY_SOURCE_RULE),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("rule_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                categorized & (transactions_table.c.category_source == CATEGORY_SOURCE_HISTORY),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("history_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                categorized & (transactions_table.c.category_source == CATEGORY_SOURCE_AI),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("ai_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                categorized & (transactions_table.c.category_source == CATEGORY_SOURCE_MANUAL),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("manual_source_count"),
                func.count(func.distinct(date_month_identity(transactions_table.c.tx_date))).label("active_months"),
                func.min(transactions_table.c.tx_date).label("first_tx_date"),
                func.max(transactions_table.c.tx_date).label("last_tx_date"),
            ).where(
                reportable_or_tagged_transfer_credit_clause(include_transfer_credits),
                *filters,
            )
        )
        .mappings()
        .fetchone()
    )


def fetch_quick_view_counts(conn: Any, filters: Sequence[Any], unknown_category: str) -> dict[str, Any]:
    """Fetch quick view counts."""
    category = func.coalesce(transactions_table.c.category, unknown_category)
    row = (
        conn.execute(
            select(
                func.count().label("all_count"),
                func.coalesce(
                    func.sum(case((transactions_table.c.needs_review == 1, 1), else_=0)),
                    0,
                ).label("needs_review_count"),
                func.coalesce(func.sum(case((category == unknown_category, 1), else_=0)), 0).label("unknown_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (category != unknown_category) & (transactions_table.c.needs_review == 0),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("categorized_count"),
            ).where(non_transfer_clause(), *filters)
        )
        .mappings()
        .fetchone()
    )

    return {
        "all_count": row["all_count"] or 0,
        "needs_review_count": row["needs_review_count"] or 0,
        "unknown_count": row["unknown_count"] or 0,
        "categorized_count": row["categorized_count"] or 0,
    }
