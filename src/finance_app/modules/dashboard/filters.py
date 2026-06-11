"""Filter parsing helpers for the dashboard feature."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, func, or_, select

from finance_app.core.constants import FILTER_MODE_INCLUDE, FILTER_MODES
from finance_app.core.periods import (
    DEFAULT_DATE_PERIOD,
    PERIOD_CUSTOM,
    DatePeriod,
    normalize_date_period,
    parse_iso_date,
)
from finance_app.core.query import CoreFilters, QueryArgs, parse_sort_direction, query_value, query_values
from finance_app.database.tables import merchants as merchants_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.accounts.filters import parse_account_id
from finance_app.modules.categories.tag_filters import transaction_tag_condition

from .constants import (
    DASHBOARD_BREAKDOWN_CATEGORY,
    DASHBOARD_BREAKDOWN_OPTIONS,
    DASHBOARD_BREAKDOWN_TAG,
    DASHBOARD_CATEGORY_SORT_CATEGORY,
    DASHBOARD_CATEGORY_SORT_SPENDING,
    DASHBOARD_CATEGORY_SORTS,
    DASHBOARD_MERCHANT_SORT_CATEGORY,
    DASHBOARD_MERCHANT_SORT_MERCHANT,
    DASHBOARD_MERCHANT_SORT_SPENDING,
    DASHBOARD_MERCHANT_SORTS,
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

    Attributes mirror the dashboard form controls and table sort options. The
    original args object is retained for URL builders that preserve unrelated
    query parameters while toggling one control.
    """

    args: QueryArgs
    period: DatePeriod
    filter_mode: str
    breakdown_mode: str
    show_untagged: bool
    show_income: bool
    merchant_sort: str
    merchant_direction: str
    category_table_sort: str
    category_table_direction: str
    selected_categories: list[str]
    selected_tags: list[str]
    selected_account_id: int | None
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

    breakdown_mode = parse_dashboard_breakdown(query_value(args, "breakdown"))
    show_untagged = breakdown_mode == DASHBOARD_BREAKDOWN_TAG and parse_dashboard_flag(
        query_value(args, "show_untagged")
    )
    selected_categories = [category.strip() for category in query_values(args, "categories") if category.strip()]
    selected_tags = [tag.strip() for tag in query_values(args, "tags") if tag.strip()]
    date_from = parse_iso_date(query_value(args, "date_from")) if period == PERIOD_CUSTOM else ""
    date_to = parse_iso_date(query_value(args, "date_to")) if period == PERIOD_CUSTOM else ""
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    merchant_sort = parse_dashboard_table_sort(
        query_value(args, "merchant_sort"),
        DASHBOARD_MERCHANT_SORTS,
        DASHBOARD_MERCHANT_SORT_SPENDING,
    )
    category_table_sort = parse_dashboard_table_sort(
        query_value(args, "category_sort"),
        DASHBOARD_CATEGORY_SORTS,
        DASHBOARD_CATEGORY_SORT_SPENDING,
    )
    return DashboardRequest(
        args=args,
        period=period,
        filter_mode=filter_mode,
        breakdown_mode=breakdown_mode,
        show_untagged=show_untagged,
        show_income=parse_dashboard_flag(query_value(args, "show_income")),
        merchant_sort=merchant_sort,
        merchant_direction=parse_sort_direction(
            query_value(args, "merchant_direction"),
            default=dashboard_table_default_direction(merchant_sort),
        ),
        category_table_sort=category_table_sort,
        category_table_direction=parse_sort_direction(
            query_value(args, "category_direction"),
            default=dashboard_table_default_direction(category_table_sort),
        ),
        selected_categories=selected_categories,
        selected_tags=selected_tags,
        selected_account_id=parse_account_id(query_value(args, "account_id")),
        merchant_search=parse_merchant_search(query_value(args, "merchant_search")),
        quick_view=parse_quick_view(query_value(args, "quick_view"), selected_categories, selected_tags),
        date_from=date_from,
        date_to=date_to,
    )


def parse_dashboard_table_sort(value: object, allowed_sorts: Collection[str], default: str) -> str:
    """Parse dashboard table sort."""
    sort = str(value or default).strip()
    return sort if sort in allowed_sorts else default


def dashboard_table_default_direction(sort: str) -> str:
    """Render table default direction."""
    text_sorts = {
        DASHBOARD_MERCHANT_SORT_MERCHANT,
        DASHBOARD_MERCHANT_SORT_CATEGORY,
        DASHBOARD_CATEGORY_SORT_CATEGORY,
    }
    return "asc" if sort in text_sorts else "desc"


def parse_dashboard_breakdown(value: object) -> str:
    """Parse the dashboard breakdown mode."""
    breakdown = str(value or "").strip()
    return breakdown if breakdown in DASHBOARD_BREAKDOWN_OPTIONS else DASHBOARD_BREAKDOWN_CATEGORY


def parse_dashboard_flag(value: object) -> bool:
    """Parse an optional dashboard boolean query flag."""
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


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


def parse_merchant_search(value: object) -> str:
    """Return a normalized merchant search term for dashboard filters."""
    return " ".join(str(value or "").strip().split())


def apply_dashboard_dimension_filters(
    filters: CoreFilters,
    selected_categories: Sequence[str],
    selected_tags: Sequence[str],
    filter_mode: str,
    unknown_category: str,
    merchant_search: str = "",
) -> None:
    """Apply category, tag, and merchant filters to dashboard criteria."""
    category_value = func.coalesce(transactions_table.c.category, unknown_category)
    include = filter_mode == FILTER_MODE_INCLUDE
    filters.add_in(category_value, selected_categories, include=include)
    filters.add(transaction_tag_condition(selected_tags or [], include=include))
    filters.add(merchant_search_condition(merchant_search))


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


def merchant_search_condition(value: object) -> Any | None:
    """Return a merchant search condition for durable keys and descriptions."""
    text = parse_merchant_search(value).casefold()
    if not text:
        return None

    merchant_key_match = exists(
        select(1)
        .select_from(merchants_table)
        .where(
            merchants_table.c.id == transactions_table.c.merchant_id,
            func.lower(merchants_table.c.merchant_key).contains(text, autoescape=True),
        )
        .correlate(transactions_table)
    )
    return or_(
        func.lower(transactions_table.c.description).contains(text, autoescape=True),
        merchant_key_match,
    )
