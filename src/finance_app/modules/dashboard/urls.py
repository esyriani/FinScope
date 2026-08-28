"""URL builders for the dashboard feature."""

from collections.abc import Sequence

from finance_app.core.analytics import (
    QUICK_VIEW_ALL,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_NEEDS_REVIEW,
    QUICK_VIEW_UNKNOWN,
)
from finance_app.core.periods import PERIOD_CUSTOM, DatePeriod
from finance_app.core.urls import build_app_url
from finance_app.modules.transactions.constants import (
    CATEGORY_STATUS_CATEGORIZED,
    CATEGORY_STATUS_UNKNOWN,
    IGNORED_FILTER_ACTIVE,
    REVIEW_FILTER_NEEDS_REVIEW,
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
    return build_app_url("transactions.transactions", **params)
