"""SQLAlchemy Core query helpers for the dashboard feature."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import case, exists, func, or_, select

from finance_app.core.category_sql import transaction_category_join_condition, transaction_category_label_expression
from finance_app.core.money import money_to_decimal
from finance_app.core.reporting import (
    income_amount_expression,
    income_or_tagged_transfer_credit_clause,
    reportable_or_tagged_transfer_credit_clause,
    reportable_transaction_clause,
    spending_impact_amount_expression,
    spending_impact_clause,
)
from finance_app.database.dates import date_month, date_month_identity, date_year, month_label
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import merchants as merchants_table
from finance_app.database.tables import (
    transaction_tags as transaction_tags_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.builtins import (
    BUILTIN_CATEGORY_TRANSFERS,
    BUILTIN_CATEGORY_UNKNOWN,
)
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_MANUAL,
    CATEGORY_SOURCE_RULE,
)
from finance_app.modules.merchants.queries import merchant_fallback_description_expression
from finance_app.modules.merchants.repository import merchant_identity_from_row


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
    category = dashboard_category_label_expression(unknown_category)
    has_tag = exists(select(1).where(transaction_tags_table.c.transaction_id == transactions_table.c.id))
    categorized = category != unknown_category
    unknown = category == unknown_category
    untagged = non_transfer_clause() & ~has_tag
    untagged_spending = spending_impact_clause() & untagged
    unknown_spending = spending_impact_clause() & non_transfer_clause() & unknown
    unknown_income = (
        (transactions_table.c.amount < 0)
        & income_or_tagged_transfer_credit_clause(include_transfer_credits)
        & reportable_or_tagged_transfer_credit_clause(include_transfer_credits)
        & unknown
    )
    spending_amount = spending_impact_amount_expression()
    income_amount = income_amount_expression()
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
                func.coalesce(func.sum(case((unknown, 1), else_=0)), 0).label("uncategorized_count"),
                func.coalesce(func.sum(case((untagged, 1), else_=0)), 0).label("untagged_count"),
                func.coalesce(func.sum(case((untagged_spending, 1), else_=0)), 0).label("untagged_spending_count"),
                func.coalesce(
                    func.sum(case((untagged_spending, spending_amount), else_=0)),
                    0,
                ).label("untagged_spending_total"),
                func.coalesce(
                    func.sum(case((unknown_spending, spending_amount), else_=0)),
                    0,
                ).label("unknown_spending_total"),
                func.coalesce(
                    func.sum(case((unknown_income, income_amount), else_=0)),
                    0,
                ).label("unknown_income_total"),
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
    category = dashboard_category_label_expression(unknown_category)
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


def dashboard_category_label_expression(unknown_category: str) -> Any:
    """Return the category label expression used by dashboard preview rows."""
    return transaction_category_label_expression(unknown_category)


def dashboard_category_join_condition(unknown_category: str) -> Any:
    """Return the category join condition with legacy cached-label fallback."""
    del unknown_category
    return transaction_category_join_condition()


def fetch_monthly_preview(
    conn: Any,
    filters: Sequence[Any],
    include_transfer_credits: bool = False,
) -> list[dict[str, Any]]:
    """Fetch compact monthly spending, income, and net cash-flow preview rows."""
    year = date_year(transactions_table.c.tx_date)
    month = date_month(transactions_table.c.tx_date)
    spending_amount = spending_impact_amount_expression()
    income_amount = income_amount_expression()
    rows = (
        conn.execute(
            select(
                year.label("year"),
                month.label("month"),
                func.coalesce(
                    func.sum(case((spending_impact_clause(), spending_amount), else_=0)),
                    0,
                ).label("spending"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (transactions_table.c.amount < 0)
                                & income_or_tagged_transfer_credit_clause(include_transfer_credits),
                                income_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("income"),
                func.count().label("transaction_count"),
            )
            .where(
                reportable_or_tagged_transfer_credit_clause(include_transfer_credits),
                *filters,
            )
            .group_by(year, month)
            .order_by(year, month)
        )
        .mappings()
        .fetchall()
    )
    return [
        {
            "label": month_label(row["year"], row["month"]),
            "spending": row["spending"],
            "income": row["income"],
            "net": money_to_decimal(row["income"]) - money_to_decimal(row["spending"]),
            "transaction_count": row["transaction_count"],
        }
        for row in rows
    ]


def fetch_spending_category_totals(
    conn: Any,
    filters: Sequence[Any],
    unknown_category: str,
) -> list[dict[str, Any]]:
    """Fetch reportable spending totals by category for dashboard previews."""
    category_label = dashboard_category_label_expression(unknown_category)
    display_label = transaction_category_label_expression(
        unknown_category,
        joined_category_name=categories_table.c.name,
    )
    spending_amount = spending_impact_amount_expression()
    rows = (
        conn.execute(
            select(
                categories_table.c.id.label("category_id"),
                display_label.label("label"),
                func.coalesce(func.sum(spending_amount), 0).label("spending"),
                func.count().label("transaction_count"),
            )
            .select_from(
                transactions_table.outerjoin(
                    categories_table,
                    dashboard_category_join_condition(unknown_category),
                )
            )
            .where(
                non_transfer_clause(),
                spending_impact_clause(),
                category_label != unknown_category,
                or_(
                    categories_table.c.builtin_key.is_(None),
                    categories_table.c.builtin_key.not_in(
                        (
                            BUILTIN_CATEGORY_UNKNOWN,
                            BUILTIN_CATEGORY_TRANSFERS,
                        )
                    ),
                ),
                *filters,
            )
            .group_by(categories_table.c.id, display_label)
            .order_by(func.sum(spending_amount).desc(), display_label)
        )
        .mappings()
        .fetchall()
    )
    return [dict(row) for row in rows]


def fetch_top_spending_categories(
    conn: Any,
    filters: Sequence[Any],
    unknown_category: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Fetch the top spending categories for the dashboard pulse."""
    return fetch_spending_category_totals(conn, filters, unknown_category)[:limit]


def fetch_spending_merchant_totals(conn: Any, filters: Sequence[Any]) -> list[dict[str, Any]]:
    """Fetch reportable spending totals by merchant identity for dashboard previews."""
    fallback_description = merchant_fallback_description_expression()
    spending_amount = spending_impact_amount_expression()
    spending_total = func.coalesce(func.sum(spending_amount), 0)
    rows = (
        conn.execute(
            select(
                fallback_description.label("description"),
                transactions_table.c.merchant_id,
                merchants_table.c.merchant_key.label("merchant_name"),
                merchants_table.c.merchant_key.label("merchant_key"),
                spending_total.label("spending"),
                func.count().label("transaction_count"),
            )
            .select_from(
                transactions_table.outerjoin(
                    merchants_table,
                    merchants_table.c.id == transactions_table.c.merchant_id,
                )
            )
            .where(
                non_transfer_clause(),
                spending_impact_clause(),
                *filters,
            )
            .group_by(transactions_table.c.merchant_id, merchants_table.c.merchant_key, fallback_description)
            .order_by(spending_total.desc(), merchants_table.c.merchant_key, fallback_description)
        )
        .mappings()
        .fetchall()
    )
    aggregates: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = merchant_identity_from_row(row, conn=conn)
        key = str(identity["key"])
        aggregate = aggregates.setdefault(
            key,
            {
                "label": identity["name"],
                "merchant_key": identity["name"],
                "merchant_id": identity["id"],
                "spending": Decimal("0"),
                "transaction_count": 0,
            },
        )
        aggregate["spending"] += money_to_decimal(row["spending"])
        aggregate["transaction_count"] += int(row["transaction_count"] or 0)

    return sorted(
        aggregates.values(),
        key=lambda row: (-money_to_decimal(row["spending"]), str(row["label"])),
    )


def fetch_top_spending_merchants(
    conn: Any,
    filters: Sequence[Any],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Fetch the top spending merchants for the dashboard pulse."""
    return fetch_spending_merchant_totals(conn, filters)[:limit]


def fetch_top_spending_changes(
    conn: Any,
    current_filters: Sequence[Any],
    previous_filters: Sequence[Any],
    unknown_category: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Fetch largest spending driver changes versus the previous comparable period."""
    category_rows = merge_change_rows(
        "category",
        fetch_spending_category_totals(conn, current_filters, unknown_category),
        fetch_spending_category_totals(conn, previous_filters, unknown_category),
        "category_id",
    )
    merchant_rows = merge_change_rows(
        "merchant",
        fetch_spending_merchant_totals(conn, current_filters),
        fetch_spending_merchant_totals(conn, previous_filters),
        "merchant_id",
    )
    rows = [*category_rows, *merchant_rows]
    rows.sort(key=lambda row: (-row["abs_change"], str(row["label"])))
    return rows[:limit]


def merge_change_rows(
    row_kind: str,
    current_rows: Sequence[dict[str, Any]],
    previous_rows: Sequence[dict[str, Any]],
    id_key: str,
) -> list[dict[str, Any]]:
    """Merge current and previous preview totals into signed change rows."""
    indexed: dict[str, dict[str, Any]] = {}
    for period_key, rows in (("current", current_rows), ("previous", previous_rows)):
        for row in rows:
            key = change_row_key(row, id_key)
            aggregate = indexed.setdefault(
                key,
                {
                    "kind": row_kind,
                    "label": str(row["label"]),
                    id_key: row.get(id_key),
                    "merchant_key": row.get("merchant_key"),
                    "current": Decimal("0"),
                    "previous": Decimal("0"),
                },
            )
            aggregate["label"] = str(row["label"])
            if row.get(id_key):
                aggregate[id_key] = row.get(id_key)
            if row.get("merchant_key"):
                aggregate["merchant_key"] = row.get("merchant_key")
            aggregate[period_key] = money_to_decimal(row["spending"])

    changes: list[dict[str, Any]] = []
    for row in indexed.values():
        current = money_to_decimal(row["current"])
        previous = money_to_decimal(row["previous"])
        change = current - previous
        if not current and not previous:
            continue
        changes.append(
            {
                **row,
                "current": current,
                "previous": previous,
                "change": change,
                "abs_change": abs(change),
            }
        )
    return changes


def change_row_key(row: dict[str, Any], id_key: str) -> str:
    """Return a stable merge key for category or merchant change rows."""
    row_id = row.get(id_key)
    if row_id:
        return f"id:{row_id}"
    return f"label:{str(row.get('label') or '').casefold()}"
