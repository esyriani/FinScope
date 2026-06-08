"""SQLAlchemy Core query helpers for the comparison feature."""

from sqlalchemy import case, func, select

from finance_app.core.reporting import (
    income_or_tagged_transfer_credit_clause,
    reportable_or_tagged_transfer_credit_clause,
    reportable_transaction_clause,
    spending_impact_clause,
)
from finance_app.database.dates import date_month, date_year
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.tag_filters import transaction_tag_condition


def non_transfer_clause():
    """Return the comparison Core filter for reportable transaction kinds."""
    return reportable_transaction_clause()


def transaction_year():
    """Return a Core expression for a transaction year."""
    return date_year(transactions_table.c.tx_date)


def transaction_month():
    """Return a Core expression for a transaction month."""
    return date_month(transactions_table.c.tx_date)


def build_category_conditions(selected_categories, selected_tags, unknown_category):
    """Build Core category and tag filter conditions."""
    conditions = []
    if selected_categories:
        conditions.append(func.coalesce(transactions_table.c.category, unknown_category).in_(selected_categories))

    tag_condition = transaction_tag_condition(selected_tags)
    if tag_condition is not None:
        conditions.append(tag_condition)

    return tuple(conditions)


def fetch_available_years(conn):
    """Fetch available years."""
    year = transaction_year()
    rows = (
        conn.execute(
            select(year.label("year"))
            .where(
                transactions_table.c.tx_date.is_not(None),
                transactions_table.c.ignored == 0,
                reportable_transaction_clause(),
            )
            .distinct()
            .order_by(year.desc())
        )
        .mappings()
        .fetchall()
    )
    return [row["year"] for row in rows if row["year"]]


def fetch_monthly_spending(conn, filters):
    """Fetch monthly spending."""
    year = transaction_year()
    month = transaction_month()
    spending = func.coalesce(
        func.sum(
            case(
                (
                    spending_impact_clause(),
                    transactions_table.c.amount,
                ),
                else_=0,
            )
        ),
        0,
    )
    return (
        conn.execute(
            select(
                year.label("year"),
                month.label("month"),
                spending.label("spending"),
            )
            .where(
                spending_impact_clause(),
                *filters,
            )
            .group_by(year, month)
            .order_by(year.desc(), month)
        )
        .mappings()
        .fetchall()
    )


def fetch_category_comparison(conn, filters, unknown_category):
    """Fetch category comparison."""
    year = transaction_year()
    category = func.coalesce(transactions_table.c.category, unknown_category)
    spending = func.coalesce(
        func.sum(
            case(
                (
                    spending_impact_clause(),
                    transactions_table.c.amount,
                ),
                else_=0,
            )
        ),
        0,
    )
    return (
        conn.execute(
            select(
                year.label("year"),
                category.label("category"),
                spending.label("spending"),
            )
            .where(
                spending_impact_clause(),
                *filters,
            )
            .group_by(year, category)
            .order_by(func.lower(category), category, year.desc())
        )
        .mappings()
        .fetchall()
    )


def fetch_period_summary(
    conn,
    date_from,
    date_to,
    category_filters,
    unknown_category,
    include_transfer_credits=False,
):
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
                                transactions_table.c.amount,
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
                                -transactions_table.c.amount,
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


def fetch_period_category_spending(conn, date_from, date_to, category_filters, unknown_category):
    """Fetch period category spending."""
    category = func.coalesce(transactions_table.c.category, unknown_category)
    return (
        conn.execute(
            select(
                category.label("category"),
                func.coalesce(func.sum(transactions_table.c.amount), 0).label("spending"),
            )
            .where(
                transactions_table.c.ignored == 0,
                spending_impact_clause(),
                transactions_table.c.tx_date >= date_from,
                transactions_table.c.tx_date <= date_to,
                *category_filters,
            )
            .group_by(category)
        )
        .mappings()
        .fetchall()
    )


def fetch_period_merchant_transactions(conn, date_from, date_to, category_filters, unknown_category):
    """Fetch period merchant transactions."""
    return (
        conn.execute(
            select(
                transactions_table.c.description,
                transactions_table.c.amount,
                func.coalesce(transactions_table.c.category, unknown_category).label("category"),
            ).where(
                transactions_table.c.ignored == 0,
                spending_impact_clause(),
                transactions_table.c.tx_date >= date_from,
                transactions_table.c.tx_date <= date_to,
                *category_filters,
            )
        )
        .mappings()
        .fetchall()
    )


def fetch_historical_monthly_category_spending(conn, date_before, category_filters, unknown_category):
    """Fetch monthly category spending before a comparison period."""
    year = transaction_year()
    month = transaction_month()
    category = func.coalesce(transactions_table.c.category, unknown_category)
    return (
        conn.execute(
            select(
                year.label("year"),
                month.label("month"),
                category.label("category"),
                func.coalesce(func.sum(transactions_table.c.amount), 0).label("spending"),
            )
            .where(
                transactions_table.c.ignored == 0,
                spending_impact_clause(),
                transactions_table.c.tx_date < date_before,
                *category_filters,
            )
            .group_by(year, month, category)
            .order_by(year.desc(), month.desc(), func.lower(category), category)
        )
        .mappings()
        .fetchall()
    )


def fetch_historical_monthly_merchant_transactions(conn, date_before, category_filters, unknown_category):
    """Fetch merchant transaction rows before a comparison period for monthly grouping."""
    year = transaction_year()
    month = transaction_month()
    return (
        conn.execute(
            select(
                year.label("year"),
                month.label("month"),
                transactions_table.c.description,
                transactions_table.c.amount,
                func.coalesce(transactions_table.c.category, unknown_category).label("category"),
            )
            .where(
                transactions_table.c.ignored == 0,
                spending_impact_clause(),
                transactions_table.c.tx_date < date_before,
                *category_filters,
            )
            .order_by(year.desc(), month.desc())
        )
        .mappings()
        .fetchall()
    )
