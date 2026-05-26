"""SQLAlchemy Core query helpers for the dashboard feature."""

from sqlalchemy import case, exists, func, select

from finance_app.core.periods import previous_period_date_range
from finance_app.core.reporting import (
    income_or_tagged_transfer_credit_clause,
    reportable_or_tagged_transfer_credit_clause,
    reportable_transaction_clause,
    spending_impact_clause,
)
from finance_app.core.query import CoreFilters
from finance_app.database.dates import date_month, date_month_identity, date_year, month_label
from finance_app.database.tables import (
    merchants as merchants_table,
    tags as tags_table,
    transaction_tags as transaction_tags_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_MANUAL,
    CATEGORY_SOURCE_RULE,
)
from finance_app.modules.categories.service import get_category_rules
from finance_app.modules.transactions.constants import AMOUNT_TYPE_SPENDING
from .filters import apply_quick_view_core_filter
from .urls import dashboard_transactions_url
from .constants import DASHBOARD_INCOME_CATEGORY, QUICK_VIEW_ALL
from .presenter import (
    build_merchant_aggregates,
    merchant_matching_rules,
    merchant_period_change,
    merchant_primary_category,
)


def non_transfer_clause():
    """Return the dashboard Core filter for reportable transaction kinds."""
    return reportable_transaction_clause()


def fetch_spending_by_category(conn, filters, unknown_category, include_income_category=False):
    """Fetch spending by category, optionally retaining rows categorized as income."""
    category = func.coalesce(transactions_table.c.category, unknown_category)
    total = func.sum(transactions_table.c.amount)
    income_filter = [] if include_income_category else [category != DASHBOARD_INCOME_CATEGORY]
    return conn.execute(
        select(
            category.label("category"),
            total.label("total"),
        )
        .where(
            spending_impact_clause(),
            non_transfer_clause(),
            category != unknown_category,
            *income_filter,
            *filters,
        )
        .group_by(category)
        .order_by(total.desc())
    ).mappings().fetchall()


def fetch_spending_by_tag(conn, filters, include_income_category=False):
    """Fetch spending totals associated with each transaction tag.

    Tagged totals intentionally count the full transaction amount for every tag
    attached to the transaction. A transaction with multiple tags can therefore
    contribute to multiple rows.
    """
    category = func.coalesce(transactions_table.c.category, "")
    total = func.sum(transactions_table.c.amount)
    income_filter = [] if include_income_category else [category != DASHBOARD_INCOME_CATEGORY]
    tagged_rows = conn.execute(
        select(
            tags_table.c.name.label("category"),
            tags_table.c.name.label("tag"),
            total.label("total"),
        )
        .select_from(
            transactions_table
            .join(
                transaction_tags_table,
                transaction_tags_table.c.transaction_id == transactions_table.c.id,
            )
            .join(tags_table, tags_table.c.id == transaction_tags_table.c.tag_id)
        )
        .where(
            spending_impact_clause(),
            non_transfer_clause(),
            *income_filter,
            *filters,
        )
        .group_by(tags_table.c.name)
        .order_by(total.desc(), tags_table.c.name)
    ).mappings().fetchall()

    has_tag = exists(
        select(1).where(transaction_tags_table.c.transaction_id == transactions_table.c.id)
    )
    untagged = conn.execute(
        select(total.label("total"))
        .where(
            spending_impact_clause(),
            non_transfer_clause(),
            ~has_tag,
            *income_filter,
            *filters,
        )
    ).mappings().fetchone()
    untagged_total = untagged["total"] if untagged else None
    rows = [dict(row) for row in tagged_rows]
    if untagged_total:
        rows.append(
            {
                "category": "Untagged",
                "tag": "",
                "total": untagged_total,
                "untagged": True,
            }
        )

    rows.sort(key=lambda row: (-float(row["total"] or 0), row["category"]))
    return rows


def fetch_monthly_expenses(conn, filters):
    """Fetch monthly expenses."""
    year = date_year(transactions_table.c.tx_date)
    month = date_month(transactions_table.c.tx_date)
    total = func.sum(transactions_table.c.amount)
    rows = conn.execute(
        select(
            year.label("year"),
            month.label("month"),
            total.label("total"),
        )
        .where(
            spending_impact_clause(),
            non_transfer_clause(),
            *filters,
        )
        .group_by(year, month)
        .order_by(year, month)
    ).mappings().fetchall()
    return month_total_rows(rows)


def fetch_monthly_income(conn, filters, include_transfer_credits=False):
    """Fetch monthly income, optionally including filtered transfer credits."""
    year = date_year(transactions_table.c.tx_date)
    month = date_month(transactions_table.c.tx_date)
    total = func.sum(-transactions_table.c.amount)
    rows = conn.execute(
        select(
            year.label("year"),
            month.label("month"),
            total.label("total"),
        )
        .where(
            transactions_table.c.amount < 0,
            income_or_tagged_transfer_credit_clause(include_transfer_credits),
            reportable_or_tagged_transfer_credit_clause(include_transfer_credits),
            *filters,
        )
        .group_by(year, month)
        .order_by(year, month)
    ).mappings().fetchall()
    return month_total_rows(rows)


def fetch_monthly_net(conn, filters, include_transfer_credits=False):
    """Fetch monthly net, optionally including filtered transfer credits."""
    year = date_year(transactions_table.c.tx_date)
    month = date_month(transactions_table.c.tx_date)
    total = func.sum(-transactions_table.c.amount)
    rows = conn.execute(
        select(
            year.label("year"),
            month.label("month"),
            total.label("total"),
        )
        .where(
            reportable_or_tagged_transfer_credit_clause(include_transfer_credits),
            *filters,
        )
        .group_by(year, month)
        .order_by(year, month)
    ).mappings().fetchall()
    return month_total_rows(rows)


def month_total_rows(rows):
    """Return dashboard month total rows with YYYY-MM labels."""
    return [
        {
            "month": month_label(row["year"], row["month"]),
            "total": row["total"],
        }
        for row in rows
    ]


def fetch_merchant_analytics(
    conn,
    period,
    filters,
    filter_mode,
    selected_categories,
    selected_tags,
    unknown_category,
    date_from="",
    date_to="",
    quick_view=QUICK_VIEW_ALL,
    merchant_table_limit=10,
):
    """Fetch merchant analytics."""
    current_rows = fetch_merchant_transaction_rows(conn, filters, unknown_category)
    aggregates = build_merchant_aggregates(current_rows, conn=conn)
    previous_totals = fetch_previous_merchant_totals(
        conn,
        period,
        filter_mode,
        selected_categories,
        selected_tags,
        unknown_category,
        quick_view,
    )
    rules = get_category_rules(conn)
    merchant_rows = []
    top_merchants = sorted(aggregates.values(), key=lambda item: item["total"], reverse=True)[:merchant_table_limit]
    max_total = top_merchants[0]["total"] if top_merchants else 0

    for aggregate in top_merchants:
        category = merchant_primary_category(aggregate)
        matching_rules = merchant_matching_rules(aggregate, rules)
        previous_total = previous_totals.get(aggregate["merchant_key"])

        merchant_rows.append(
            {
                "merchant": aggregate["merchant_key"],
                "examples": sorted(aggregate["examples"])[:3],
                "transaction_count": aggregate["transaction_count"],
                "total": round(aggregate["total"], 2),
                "bar_width": round((aggregate["total"] / max_total) * 100, 1) if max_total else 0,
                "category": category["label"],
                "category_count": category["count"],
                "category_total": round(category["total"], 2),
                "rules": matching_rules,
                "period_change": merchant_period_change(aggregate["total"], previous_total),
                "url": dashboard_transactions_url(
                    period,
                    filter_mode,
                    selected_categories,
                    True,
                    date_from,
                    date_to,
                    quick_view,
                    selected_tags=selected_tags,
                    merchant_key=aggregate["merchant_key"],
                    amount_type=AMOUNT_TYPE_SPENDING,
                ),
            }
        )

    return merchant_rows


def fetch_merchant_transaction_rows(conn, filters, unknown_category):
    """Fetch merchant transaction rows."""
    return conn.execute(
        select(
            transactions_table.c.description,
            transactions_table.c.merchant_id,
            merchants_table.c.display_name.label("merchant_name"),
            merchants_table.c.canonical_key.label("merchant_canonical_key"),
            transactions_table.c.amount,
            func.coalesce(transactions_table.c.category, unknown_category).label("category"),
        )
        .select_from(
            transactions_table.outerjoin(
                merchants_table,
                merchants_table.c.id == transactions_table.c.merchant_id,
            )
        )
        .where(
            spending_impact_clause(),
            non_transfer_clause(),
            *filters,
        )
    ).mappings().fetchall()


def fetch_previous_merchant_totals(
    conn,
    period,
    filter_mode,
    selected_categories,
    selected_tags,
    unknown_category,
    quick_view=QUICK_VIEW_ALL,
):
    """Fetch previous merchant totals."""
    previous_start, previous_end = previous_period_date_range(period)
    if previous_start is None or previous_end is None:
        return {}

    filters = CoreFilters()
    filters.add(transactions_table.c.ignored == 0)
    filters.add(transactions_table.c.tx_date >= previous_start)
    filters.add(transactions_table.c.tx_date < previous_end)
    apply_quick_view_core_filter(
        filters,
        quick_view,
        selected_categories,
        selected_tags,
        filter_mode,
        unknown_category,
    )
    aggregates = build_merchant_aggregates(
        fetch_merchant_transaction_rows(
            conn,
            filters.criteria(),
            unknown_category,
        ),
        conn=conn,
    )

    return {
        merchant_key: aggregate["total"]
        for merchant_key, aggregate in aggregates.items()
    }


def fetch_summary(conn, filters, unknown_category, include_transfer_credits=False):
    """Fetch dashboard summary totals, optionally including filtered transfer credits."""
    category = func.coalesce(transactions_table.c.category, unknown_category)
    has_tag = exists(
        select(1).where(transaction_tags_table.c.transaction_id == transactions_table.c.id)
    )
    categorized = category != unknown_category
    untagged_spending = spending_impact_clause() & non_transfer_clause() & ~has_tag
    return conn.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            spending_impact_clause() & non_transfer_clause(),
                            transactions_table.c.amount,
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
                            -transactions_table.c.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("total_income"),
            func.count().label("transaction_count"),
            func.coalesce(func.avg(func.abs(transactions_table.c.amount)), 0).label("average_transaction_amount"),
            func.coalesce(func.sum(case((category == unknown_category, 1), else_=0)), 0).label("uncategorized_count"),
            func.coalesce(func.sum(case((untagged_spending, 1), else_=0)), 0).label("untagged_spending_count"),
            func.coalesce(
                func.sum(case((untagged_spending, transactions_table.c.amount), else_=0)),
                0,
            ).label("untagged_spending_total"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (transactions_table.c.needs_review == 1)
                            & (category == unknown_category),
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
                            categorized
                            & (transactions_table.c.category_source == CATEGORY_SOURCE_RULE),
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
                            categorized
                            & (transactions_table.c.category_source == CATEGORY_SOURCE_HISTORY),
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
                            categorized
                            & (transactions_table.c.category_source == CATEGORY_SOURCE_AI),
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
                            categorized
                            & (transactions_table.c.category_source == CATEGORY_SOURCE_MANUAL),
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
        )
        .where(
            reportable_or_tagged_transfer_credit_clause(include_transfer_credits),
            *filters,
        )
    ).mappings().fetchone()


def fetch_quick_view_counts(conn, filters, unknown_category):
    """Fetch quick view counts."""
    category = func.coalesce(transactions_table.c.category, unknown_category)
    row = conn.execute(
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
                            (category != unknown_category)
                            & (transactions_table.c.needs_review == 0),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("categorized_count"),
        )
        .where(non_transfer_clause(), *filters)
    ).mappings().fetchone()

    return {
        "all_count": row["all_count"] or 0,
        "needs_review_count": row["needs_review_count"] or 0,
        "unknown_count": row["unknown_count"] or 0,
        "categorized_count": row["categorized_count"] or 0,
    }
