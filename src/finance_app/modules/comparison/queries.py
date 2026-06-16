"""SQLAlchemy Core query helpers for the comparison feature."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import and_, case, func, select

from finance_app.core.reporting import (
    cashflow_amount_expression,
    income_amount_expression,
    income_or_tagged_transfer_credit_clause,
    reportable_or_tagged_transfer_credit_clause,
    reportable_transaction_clause,
    spending_impact_amount_expression,
    spending_impact_clause,
)
from finance_app.database.dates import date_month, date_year
from finance_app.database.tables import merchants as merchants_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.accounts.filters import account_filter_condition
from finance_app.modules.categories.tag_filters import transaction_tag_condition
from finance_app.modules.comparison.constants import ANALYSIS_MODE_INCOME, ANALYSIS_MODE_NET
from finance_app.modules.merchants.filters import merchant_filter_condition


def non_transfer_clause() -> Any:
    """Return the comparison Core filter for reportable transaction kinds."""
    return reportable_transaction_clause()


def transaction_year() -> Any:
    """Return a Core expression for a transaction year."""
    return date_year(transactions_table.c.tx_date)


def transaction_month() -> Any:
    """Return a Core expression for a transaction month."""
    return date_month(transactions_table.c.tx_date)


def analysis_amount_expression(analysis_mode: str) -> Any:
    """Return the signed amount expression for the selected analysis mode."""
    if analysis_mode == ANALYSIS_MODE_INCOME:
        return income_amount_expression()
    if analysis_mode == ANALYSIS_MODE_NET:
        return cashflow_amount_expression()
    return spending_impact_amount_expression()


def analysis_scope_clause(analysis_mode: str, include_transfer_credits: bool = False) -> Any:
    """Return the row scope for the selected analysis mode."""
    if analysis_mode == ANALYSIS_MODE_INCOME:
        return and_(
            transactions_table.c.amount < 0,
            income_or_tagged_transfer_credit_clause(include_transfer_credits),
            reportable_or_tagged_transfer_credit_clause(include_transfer_credits),
        )
    if analysis_mode == ANALYSIS_MODE_NET:
        return reportable_or_tagged_transfer_credit_clause(include_transfer_credits)
    return spending_impact_clause()


def build_category_conditions(
    selected_categories: Iterable[str],
    selected_tags: Iterable[str],
    unknown_category: str,
    account_id: int | None = None,
    merchant_id: int | None = None,
    merchant_query: str = "",
) -> tuple[Any, ...]:
    """Build Core category and tag filter conditions."""
    conditions: list[Any] = []
    account_condition = account_filter_condition(account_id)
    if account_condition is not None:
        conditions.append(account_condition)
    merchant_condition = merchant_filter_condition(merchant_id, merchant_query)
    if merchant_condition is not None:
        conditions.append(merchant_condition)
    if selected_categories:
        conditions.append(func.coalesce(transactions_table.c.category, unknown_category).in_(selected_categories))

    tag_condition = transaction_tag_condition(selected_tags)
    if tag_condition is not None:
        conditions.append(tag_condition)

    return tuple(conditions)


def fetch_available_years(
    conn: Any,
    account_id: int | None = None,
    merchant_id: int | None = None,
    merchant_query: str = "",
) -> list[int]:
    """Fetch available years."""
    year = transaction_year()
    rows = (
        conn.execute(
            select(year.label("year"))
            .where(
                transactions_table.c.tx_date.is_not(None),
                transactions_table.c.ignored == 0,
                reportable_transaction_clause(),
                *[
                    condition
                    for condition in (
                        account_filter_condition(account_id),
                        merchant_filter_condition(merchant_id, merchant_query),
                    )
                    if condition is not None
                ],
            )
            .distinct()
            .order_by(year.desc())
        )
        .mappings()
        .fetchall()
    )
    return [row["year"] for row in rows if row["year"]]


def fetch_monthly_analysis(
    conn: Any,
    filters: Iterable[Any],
    analysis_mode: str,
    include_transfer_credits: bool = False,
) -> list[Any]:
    """Fetch monthly analysis totals."""
    year = transaction_year()
    month = transaction_month()
    amount = func.coalesce(func.sum(analysis_amount_expression(analysis_mode)), 0)
    return (
        conn.execute(
            select(
                year.label("year"),
                month.label("month"),
                amount.label("amount"),
            )
            .where(
                analysis_scope_clause(analysis_mode, include_transfer_credits),
                *filters,
            )
            .group_by(year, month)
            .order_by(year.desc(), month)
        )
        .mappings()
        .fetchall()
    )


def fetch_category_comparison(
    conn: Any,
    filters: Iterable[Any],
    unknown_category: str,
    analysis_mode: str,
    include_transfer_credits: bool = False,
) -> list[Any]:
    """Fetch category comparison totals."""
    year = transaction_year()
    category = func.coalesce(transactions_table.c.category, unknown_category)
    amount = func.coalesce(func.sum(analysis_amount_expression(analysis_mode)), 0)
    return (
        conn.execute(
            select(
                year.label("year"),
                category.label("category"),
                amount.label("amount"),
            )
            .where(
                analysis_scope_clause(analysis_mode, include_transfer_credits),
                *filters,
            )
            .group_by(year, category)
            .order_by(func.lower(category), category, year.desc())
        )
        .mappings()
        .fetchall()
    )


def fetch_period_summary(
    conn: Any,
    date_from: str,
    date_to: str,
    category_filters: Iterable[Any],
    unknown_category: str,
    include_transfer_credits: bool = False,
) -> Any:
    """Fetch period summary, optionally including filtered transfer credits."""
    del unknown_category
    return (
        conn.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                spending_impact_clause(),
                                spending_impact_amount_expression(),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("spending"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (transactions_table.c.amount < 0)
                                & income_or_tagged_transfer_credit_clause(include_transfer_credits),
                                income_amount_expression(),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("income"),
                func.count().label("transaction_count"),
            ).where(
                transactions_table.c.ignored == 0,
                reportable_or_tagged_transfer_credit_clause(include_transfer_credits),
                transactions_table.c.tx_date >= date_from,
                transactions_table.c.tx_date <= date_to,
                *category_filters,
            )
        )
        .mappings()
        .fetchone()
    )


def fetch_period_category_analysis(
    conn: Any,
    date_from: str,
    date_to: str,
    category_filters: Iterable[Any],
    unknown_category: str,
    analysis_mode: str,
    include_transfer_credits: bool = False,
) -> list[Any]:
    """Fetch period category totals for the selected analysis mode."""
    category = func.coalesce(transactions_table.c.category, unknown_category)
    return (
        conn.execute(
            select(
                category.label("category"),
                func.coalesce(func.sum(analysis_amount_expression(analysis_mode)), 0).label("amount"),
            )
            .where(
                transactions_table.c.ignored == 0,
                analysis_scope_clause(analysis_mode, include_transfer_credits),
                transactions_table.c.tx_date >= date_from,
                transactions_table.c.tx_date <= date_to,
                *category_filters,
            )
            .group_by(category)
        )
        .mappings()
        .fetchall()
    )


def fetch_period_merchant_transactions(
    conn: Any,
    date_from: str,
    date_to: str,
    category_filters: Iterable[Any],
    unknown_category: str,
    analysis_mode: str,
    include_transfer_credits: bool = False,
) -> list[Any]:
    """Fetch period merchant transactions for the selected analysis mode."""
    return (
        conn.execute(
            select(
                transactions_table.c.description,
                transactions_table.c.merchant_id,
                merchants_table.c.merchant_key.label("merchant_name"),
                merchants_table.c.merchant_key.label("merchant_key"),
                analysis_amount_expression(analysis_mode).label("amount"),
                func.coalesce(transactions_table.c.category, unknown_category).label("category"),
            )
            .select_from(
                transactions_table.outerjoin(
                    merchants_table,
                    merchants_table.c.id == transactions_table.c.merchant_id,
                )
            )
            .where(
                transactions_table.c.ignored == 0,
                analysis_scope_clause(analysis_mode, include_transfer_credits),
                transactions_table.c.tx_date >= date_from,
                transactions_table.c.tx_date <= date_to,
                *category_filters,
            )
        )
        .mappings()
        .fetchall()
    )


def fetch_historical_monthly_category_analysis(
    conn: Any,
    date_before: str,
    category_filters: Iterable[Any],
    unknown_category: str,
    analysis_mode: str,
    include_transfer_credits: bool = False,
) -> list[Any]:
    """Fetch monthly category totals before a comparison period."""
    year = transaction_year()
    month = transaction_month()
    category = func.coalesce(transactions_table.c.category, unknown_category)
    return (
        conn.execute(
            select(
                year.label("year"),
                month.label("month"),
                category.label("category"),
                func.coalesce(func.sum(analysis_amount_expression(analysis_mode)), 0).label("amount"),
            )
            .where(
                transactions_table.c.ignored == 0,
                analysis_scope_clause(analysis_mode, include_transfer_credits),
                transactions_table.c.tx_date < date_before,
                *category_filters,
            )
            .group_by(year, month, category)
            .order_by(year.desc(), month.desc(), func.lower(category), category)
        )
        .mappings()
        .fetchall()
    )


def fetch_historical_monthly_merchant_transactions(
    conn: Any,
    date_before: str,
    category_filters: Iterable[Any],
    unknown_category: str,
    analysis_mode: str,
    include_transfer_credits: bool = False,
) -> list[Any]:
    """Fetch merchant analysis rows before a comparison period for monthly grouping."""
    year = transaction_year()
    month = transaction_month()
    return (
        conn.execute(
            select(
                year.label("year"),
                month.label("month"),
                transactions_table.c.description,
                transactions_table.c.merchant_id,
                merchants_table.c.merchant_key.label("merchant_name"),
                merchants_table.c.merchant_key.label("merchant_key"),
                analysis_amount_expression(analysis_mode).label("amount"),
                func.coalesce(transactions_table.c.category, unknown_category).label("category"),
            )
            .select_from(
                transactions_table.outerjoin(
                    merchants_table,
                    merchants_table.c.id == transactions_table.c.merchant_id,
                )
            )
            .where(
                transactions_table.c.ignored == 0,
                analysis_scope_clause(analysis_mode, include_transfer_credits),
                transactions_table.c.tx_date < date_before,
                *category_filters,
            )
            .order_by(year.desc(), month.desc())
        )
        .mappings()
        .fetchall()
    )
