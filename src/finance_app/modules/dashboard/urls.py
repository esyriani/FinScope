"""URL builders for the dashboard feature."""

from calendar import monthrange
from collections.abc import Sequence
from typing import Any, Protocol, TypedDict
from urllib.parse import urlencode

from flask import url_for

from finance_app.core.periods import PERIOD_CUSTOM, DatePeriod
from finance_app.modules.transactions.constants import (
    CATEGORY_STATUS_CATEGORIZED,
    CATEGORY_STATUS_UNKNOWN,
    IGNORED_FILTER_ACTIVE,
    REVIEW_FILTER_NEEDS_REVIEW,
)

from .constants import (
    DASHBOARD_CATEGORY_SORT_SPENDING,
    DASHBOARD_CATEGORY_SORTS,
    DASHBOARD_MERCHANT_SORT_SPENDING,
    DASHBOARD_MERCHANT_SORTS,
    DASHBOARD_TABLE_MERCHANT,
    QUICK_VIEW_ALL,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_NEEDS_REVIEW,
    QUICK_VIEW_UNKNOWN,
)
from .filters import dashboard_table_default_direction, parse_dashboard_table_sort


class QueryStringArgs(Protocol):
    """Represent query args that can be copied for URL updates."""

    def to_dict(self, flat: bool = True) -> dict[str, Any]:
        """Return query parameters as a dictionary."""
        ...


class MonthBounds(TypedDict):
    """Represent inclusive month date bounds."""

    start: str
    end: str


def app_url(endpoint: str, **params: object) -> str:
    """Build an application URL with blank query values removed."""
    cleaned: dict[str, object] = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            values = [item for item in value if item not in (None, "")]
            if values:
                cleaned[key] = values
        elif value not in (None, ""):
            cleaned[key] = value

    query = urlencode(cleaned, doseq=True)
    base_url = url_for(endpoint)
    return f"{base_url}?{query}" if query else base_url


def dashboard_url(args: QueryStringArgs, **overrides: object) -> str:
    """Build a dashboard URL that preserves current query parameters."""
    query = args.to_dict(flat=False)
    for key, value in overrides.items():
        if value in (None, ""):
            query.pop(key, None)
        elif isinstance(value, (list, tuple)):
            query[key] = [str(item) for item in value if item not in (None, "")]
        else:
            query[key] = [str(value)]

    encoded_query = urlencode(query, doseq=True)
    return url_for("dashboard.dashboard") + (f"?{encoded_query}" if encoded_query else "")


def dashboard_table_sort_url(
    args: QueryStringArgs,
    table: str,
    sort_name: str,
    current_sort: str,
    current_direction: str,
) -> str:
    """Build a dashboard URL for toggling one table sort."""
    if table == DASHBOARD_TABLE_MERCHANT:
        sort_name = parse_dashboard_table_sort(
            sort_name,
            DASHBOARD_MERCHANT_SORTS,
            DASHBOARD_MERCHANT_SORT_SPENDING,
        )
    else:
        sort_name = parse_dashboard_table_sort(
            sort_name,
            DASHBOARD_CATEGORY_SORTS,
            DASHBOARD_CATEGORY_SORT_SPENDING,
        )

    next_direction = (
        ("desc" if current_direction == "asc" else "asc")
        if current_sort == sort_name
        else dashboard_table_default_direction(sort_name)
    )

    return dashboard_url(
        args,
        **{
            f"{table}_sort": sort_name,
            f"{table}_direction": next_direction,
        },
    )


def dashboard_transaction_params(
    period: DatePeriod,
    filter_mode: str,
    selected_categories: Sequence[str],
    include_category_filter: bool = True,
    date_from: str = "",
    date_to: str = "",
    quick_view: str = QUICK_VIEW_ALL,
    selected_tags: Sequence[str] | None = None,
    merchant_search: str = "",
    account_id: int | None = None,
) -> dict[str, object]:
    """Build transaction-list query parameters for a dashboard drill-down."""
    selected_tags = selected_tags or ()
    params: dict[str, object] = {
        "period": period,
        "ignored": IGNORED_FILTER_ACTIVE,
    }
    if period == PERIOD_CUSTOM:
        params["date_from"] = date_from
        params["date_to"] = date_to
    if quick_view == QUICK_VIEW_NEEDS_REVIEW:
        params["review"] = REVIEW_FILTER_NEEDS_REVIEW
    elif quick_view == QUICK_VIEW_UNKNOWN:
        params["category_status"] = CATEGORY_STATUS_UNKNOWN
    elif quick_view == QUICK_VIEW_CATEGORIZED:
        params["category_status"] = CATEGORY_STATUS_CATEGORIZED
    if (include_category_filter and selected_categories) or selected_tags:
        params["filter_mode"] = filter_mode
    if selected_tags:
        params["tags"] = selected_tags
    if include_category_filter and selected_categories:
        params["categories"] = selected_categories
    if merchant_search:
        params["search"] = merchant_search
    if account_id:
        params["account_id"] = account_id

    return params


def dashboard_transactions_url(
    period: DatePeriod,
    filter_mode: str,
    selected_categories: Sequence[str],
    include_category_filter: bool = True,
    date_from: str = "",
    date_to: str = "",
    quick_view: str = QUICK_VIEW_ALL,
    selected_tags: Sequence[str] | None = None,
    merchant_search: str = "",
    account_id: int | None = None,
    **overrides: object,
) -> str:
    """Build a transactions URL for a dashboard drill-down."""
    params = dashboard_transaction_params(
        period,
        filter_mode,
        selected_categories,
        include_category_filter=include_category_filter,
        date_from=date_from,
        date_to=date_to,
        quick_view=quick_view,
        selected_tags=selected_tags,
        merchant_search=merchant_search,
        account_id=account_id,
    )
    params.update(overrides)
    return app_url("transactions.transactions", **params)


def dashboard_month_url(
    month: str,
    filter_mode: str,
    selected_categories: Sequence[str],
    range_from: str = "",
    range_to: str = "",
    quick_view: str = QUICK_VIEW_ALL,
    selected_tags: Sequence[str] | None = None,
    merchant_search: str = "",
    account_id: int | None = None,
    **overrides: object,
) -> str:
    """Build a dashboard drill-down URL constrained to one calendar month."""
    bounds = month_bounds(month)
    date_from = max(bounds["start"], range_from) if range_from else bounds["start"]
    date_to = min(bounds["end"], range_to) if range_to else bounds["end"]
    return dashboard_transactions_url(
        PERIOD_CUSTOM,
        filter_mode,
        selected_categories,
        True,
        date_from,
        date_to,
        quick_view,
        selected_tags=selected_tags,
        merchant_search=merchant_search,
        account_id=account_id,
        **overrides,
    )


def month_bounds(month: str) -> MonthBounds:
    """Return inclusive ISO date bounds for a YYYY-MM month label."""
    year, month_number = (int(part) for part in month.split("-", 1))
    last_day = monthrange(year, month_number)[1]
    return {
        "start": f"{year:04d}-{month_number:02d}-01",
        "end": f"{year:04d}-{month_number:02d}-{last_day:02d}",
    }
