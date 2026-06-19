"""Filter parsing helpers for the dashboard feature."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func

from finance_app.core.constants import FILTER_MODE_INCLUDE, FILTER_MODES
from finance_app.core.periods import (
    DEFAULT_DATE_PERIOD,
    PERIOD_CUSTOM,
    DatePeriod,
    normalize_date_period,
    parse_iso_date,
)
from finance_app.core.query import CoreFilters, QueryArgs, query_value, query_values
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.accounts.filters import parse_account_id
from finance_app.modules.categories.tag_filters import transaction_tag_condition
from finance_app.modules.merchants.filters import (
    merchant_filter_condition,
    parse_merchant_id,
    parse_merchant_query,
)

from .constants import (
    QUICK_VIEW_ALL,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_CUSTOM,
    QUICK_VIEW_NEEDS_REVIEW,
    QUICK_VIEW_OPTIONS,
    QUICK_VIEW_UNKNOWN,
)


@dataclass(frozen=True)
class DashboardRequest:
    """Represent parsed dashboard query parameters.

    Attributes mirror the dashboard filter controls and shared drill-down
    parameters used by summary links.
    """

    args: QueryArgs
    period: DatePeriod
    filter_mode: str
    selected_categories: list[str]
    selected_tags: list[str]
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
    filter_mode = query_value(args, "filter_mode", FILTER_MODE_INCLUDE).strip()
    if filter_mode not in FILTER_MODES:
        filter_mode = FILTER_MODE_INCLUDE

    selected_categories = [category.strip() for category in query_values(args, "categories") if category.strip()]
    selected_tags = [tag.strip() for tag in query_values(args, "tags") if tag.strip()]
    date_from = parse_iso_date(query_value(args, "date_from")) if period == PERIOD_CUSTOM else ""
    date_to = parse_iso_date(query_value(args, "date_to")) if period == PERIOD_CUSTOM else ""
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    merchant_query = parse_merchant_query(query_value(args, "merchant_query"))
    return DashboardRequest(
        args=args,
        period=period,
        filter_mode=filter_mode,
        selected_categories=selected_categories,
        selected_tags=selected_tags,
        selected_account_id=parse_account_id(query_value(args, "account_id")),
        selected_merchant_id=parse_merchant_id(query_value(args, "merchant_id")),
        merchant_query=merchant_query,
        merchant_search=merchant_query,
        quick_view=parse_quick_view(query_value(args, "quick_view"), selected_categories, selected_tags),
        date_from=date_from,
        date_to=date_to,
    )


def parse_quick_view(
    value: object,
    selected_categories: Sequence[str],
    selected_tags: Sequence[str] | None = None,
) -> str:
    """Parse quick view."""
    del selected_categories, selected_tags
    quick_view = str(value or "").strip()
    if quick_view == QUICK_VIEW_CUSTOM:
        return QUICK_VIEW_ALL
    if quick_view in QUICK_VIEW_OPTIONS:
        return quick_view
    return QUICK_VIEW_CATEGORIZED


def apply_dashboard_dimension_filters(
    filters: CoreFilters,
    selected_categories: Sequence[str],
    selected_tags: Sequence[str],
    filter_mode: str,
    unknown_category: str,
    merchant_id: int | None = None,
    merchant_query: str = "",
) -> None:
    """Apply category, tag, and merchant filters to dashboard criteria."""
    category_value = func.coalesce(transactions_table.c.category, unknown_category)
    include = filter_mode == FILTER_MODE_INCLUDE
    filters.add_in(category_value, selected_categories, include=include)
    filters.add(transaction_tag_condition(selected_tags or [], include=include))
    filters.add(merchant_filter_condition(merchant_id, merchant_query))


def apply_quick_view_core_filter(
    filters: CoreFilters,
    quick_view: str,
    selected_categories: Sequence[str],
    selected_tags: Sequence[str],
    filter_mode: str,
    unknown_category: str,
) -> None:
    """Apply a quick-view status shortcut to SQLAlchemy Core filters."""
    del selected_categories, selected_tags, filter_mode
    category_value = func.coalesce(transactions_table.c.category, unknown_category)

    if quick_view == QUICK_VIEW_NEEDS_REVIEW:
        filters.add(transactions_table.c.needs_review == 1)
    elif quick_view == QUICK_VIEW_UNKNOWN:
        filters.add(category_value == unknown_category)
    elif quick_view == QUICK_VIEW_CATEGORIZED:
        filters.add(category_value != unknown_category)
        filters.add(transactions_table.c.needs_review == 0)
