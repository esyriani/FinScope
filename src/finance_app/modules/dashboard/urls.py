"""URL builders for the dashboard feature."""

from calendar import monthrange
from urllib.parse import urlencode

from flask import url_for

from finance_app.core.periods import PERIOD_CUSTOM
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


def app_url(endpoint, **params):
    """Handle app URL."""
    cleaned = {}
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


def dashboard_url(args, **overrides):
    """Render url."""
    query = args.to_dict(flat=False)
    for key, value in overrides.items():
        if value in (None, ""):
            query.pop(key, None)
        elif isinstance(value, (list, tuple)):
            query[key] = [item for item in value if item not in (None, "")]
        else:
            query[key] = [value]

    encoded_query = urlencode(query, doseq=True)
    return url_for("dashboard.dashboard") + (f"?{encoded_query}" if encoded_query else "")


def dashboard_table_sort_url(args, table, sort_name, current_sort, current_direction):
    """Render table sort URL."""
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
    period,
    filter_mode,
    selected_categories,
    include_category_filter=True,
    date_from="",
    date_to="",
    quick_view=QUICK_VIEW_ALL,
    selected_tags=None,
    merchant_search="",
):
    """Render transaction params."""
    selected_tags = selected_tags or []
    params = {
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

    return params


def dashboard_transactions_url(
    period,
    filter_mode,
    selected_categories,
    include_category_filter=True,
    date_from="",
    date_to="",
    quick_view=QUICK_VIEW_ALL,
    selected_tags=None,
    merchant_search="",
    **overrides,
):
    """Render transactions URL."""
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
    )
    params.update(overrides)
    return app_url("transactions.transactions", **params)


def dashboard_month_url(
    month,
    filter_mode,
    selected_categories,
    range_from="",
    range_to="",
    quick_view=QUICK_VIEW_ALL,
    selected_tags=None,
    merchant_search="",
    **overrides,
):
    """Render month URL."""
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
        **overrides,
    )


def month_bounds(month):
    """Return bounds."""
    year, month_number = (int(part) for part in month.split("-", 1))
    last_day = monthrange(year, month_number)[1]
    return {
        "start": f"{year:04d}-{month_number:02d}-01",
        "end": f"{year:04d}-{month_number:02d}-{last_day:02d}",
    }
