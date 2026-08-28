"""Filter parsing helpers for the dashboard feature."""

from dataclasses import dataclass

from finance_app.core.analytics import (
    QUICK_VIEW_ALL,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_CUSTOM,
)
from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.periods import (
    DEFAULT_DATE_PERIOD,
    PERIOD_CUSTOM,
    DatePeriod,
    normalize_date_period,
    parse_iso_date,
)
from finance_app.core.query import CoreFilters, QueryArgs, query_value
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.accounts.filters import parse_account_id
from finance_app.modules.merchants.filters import (
    merchant_filter_condition,
    parse_merchant_id,
    parse_merchant_query,
)


@dataclass(frozen=True)
class DashboardRequest:
    """Represent parsed dashboard query parameters.

    Attributes mirror the dashboard filter controls and shared parameters used
    by summary, report, and transaction links.
    """

    args: QueryArgs
    period: DatePeriod
    selected_account_id: int | None
    selected_merchant_id: int | None
    merchant_query: str
    merchant_search: str
    quick_view: str
    date_from: str
    date_to: str


def parse_dashboard_request(args: QueryArgs) -> DashboardRequest:
    """Return normalized dashboard query parameters."""
    period = normalize_date_period(query_value(args, "period", DEFAULT_DATE_PERIOD).strip())
    date_from = parse_iso_date(query_value(args, "date_from")) if period == PERIOD_CUSTOM else ""
    date_to = parse_iso_date(query_value(args, "date_to")) if period == PERIOD_CUSTOM else ""
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    merchant_query = parse_merchant_query(query_value(args, "merchant_query"))
    return DashboardRequest(
        args=args,
        period=period,
        selected_account_id=parse_account_id(query_value(args, "account_id")),
        selected_merchant_id=parse_merchant_id(query_value(args, "merchant_id")),
        merchant_query=merchant_query,
        merchant_search=merchant_query,
        quick_view=parse_quick_view(query_value(args, "quick_view")),
        date_from=date_from,
        date_to=date_to,
    )


def parse_quick_view(value: object) -> str:
    """Parse the Dashboard classification scope."""
    quick_view = str(value or "").strip()
    if quick_view == QUICK_VIEW_CUSTOM:
        return QUICK_VIEW_CATEGORIZED
    if quick_view in {QUICK_VIEW_ALL, QUICK_VIEW_CATEGORIZED}:
        return quick_view
    return QUICK_VIEW_CATEGORIZED


def apply_dashboard_dimension_filters(
    filters: CoreFilters,
    merchant_id: int | None = None,
    merchant_query: str = "",
) -> None:
    """Apply merchant filters to dashboard criteria."""
    filters.add(merchant_filter_condition(merchant_id, merchant_query))


def apply_quick_view_core_filter(
    filters: CoreFilters,
    quick_view: str,
    unknown_category: str,
) -> None:
    """Apply a quick-view status shortcut to SQLAlchemy Core filters."""
    category_value = transaction_category_label_expression(unknown_category)

    if quick_view == QUICK_VIEW_CATEGORIZED:
        filters.add(category_value != unknown_category)
        filters.add(transactions_table.c.needs_review == 0)
