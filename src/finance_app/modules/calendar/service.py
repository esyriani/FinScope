"""Application orchestration for the calendar feature."""

from datetime import date
from typing import Any

from finance_app.core.i18n import format_month_year, weekday_abbreviation_labels
from finance_app.modules.accounts.filters import parse_account_id
from finance_app.modules.merchants.filters import (
    parse_merchant_id,
    parse_merchant_query,
)
from finance_app.modules.recurring.activity import build_recurring_activity_context

from .parsing import clean_categories, clean_tags, default_month, parse_heatmap_metric, parse_month, shift_month
from .presenter import (
    apply_heatmap,
    build_calendar_day_json,
    build_calendar_days,
)
from .urls import calendar_url, recurring_url, transactions_url


def build_calendar_context(args: Any) -> dict[str, Any]:
    """Build calendar context."""
    selected_month = parse_month(args.get("month")) or default_month()
    heatmap_metric = parse_heatmap_metric(args.get("heatmap"))
    selected_categories = clean_categories(args.getlist("categories"))
    selected_tags = clean_tags(args.getlist("tags"))
    selected_account_id = parse_account_id(args.get("account_id"))
    selected_merchant_id = parse_merchant_id(args.get("merchant_id"))
    merchant_query = parse_merchant_query(args.get("merchant_query"))
    recurring_context = build_recurring_activity_context(
        selected_month,
        selected_categories,
        selected_tags,
        selected_account_id,
        selected_merchant_id,
        merchant_query,
    )
    month_start = recurring_context["month_start"]
    month_end = recurring_context["month_end"]
    selected_merchant_label = recurring_context["selected_merchant_label"]
    transaction_merchant_search = selected_merchant_label if selected_merchant_id else merchant_query
    days = build_calendar_days(
        month_start,
        recurring_context["transactions"],
        account_id=selected_account_id,
        merchant_search=transaction_merchant_search,
    )
    apply_heatmap(days, heatmap_metric)
    navigation_params = {
        "heatmap": heatmap_metric,
        "categories": selected_categories,
        "tags": selected_tags,
        "account_id": selected_account_id,
        "merchant_id": selected_merchant_id,
        "merchant_query": merchant_query,
    }
    has_account_filter = selected_account_id is not None
    has_merchant_filter = bool(selected_merchant_id or merchant_query)
    has_applied_filters = bool(selected_categories or selected_tags or has_account_filter or has_merchant_filter)

    return dict(
        selected_month=month_start.isoformat()[:7],
        selected_categories=selected_categories,
        selected_tags=selected_tags,
        selected_account_id=selected_account_id,
        selected_merchant_id=selected_merchant_id,
        merchant_query=merchant_query,
        selected_merchant_label=selected_merchant_label,
        merchant_suggestion_limit=recurring_context["merchant_suggestion_limit"],
        account_options=recurring_context["account_options"],
        category_options=recurring_context["category_options"],
        tag_options=recurring_context["tag_options"],
        heatmap_metric=heatmap_metric,
        heatmap_options=[
            {"value": "spending", "label": "Spending"},
            {"value": "income", "label": "Income"},
            {"value": "net", "label": "Net cash flow"},
        ],
        month_label=format_month_year(month_start),
        previous_month_url=calendar_url(shift_month(month_start, -1), navigation_params),
        next_month_url=calendar_url(shift_month(month_start, 1), navigation_params),
        current_month_url=calendar_url(default_month(), navigation_params),
        today=date.today().isoformat(),
        days=days,
        weekday_labels=weekday_abbreviation_labels(),
        summary=recurring_context["summary"],
        month_transactions_url=transactions_url(
            month_start.isoformat(),
            month_end.isoformat(),
            account_id=selected_account_id,
            merchant_search=transaction_merchant_search,
        ),
        recurring_calendar_url=recurring_url(
            month_start.isoformat()[:7],
            "calendar",
            account_id=selected_account_id,
            merchant_id=selected_merchant_id,
            merchant_query=merchant_query,
        ),
        recurring_list_url=recurring_url(
            month_start.isoformat()[:7],
            "list",
            account_id=selected_account_id,
            merchant_id=selected_merchant_id,
            merchant_query=merchant_query,
        ),
        calendar_empty_state_message=calendar_empty_state_message(
            has_applied_filters,
            has_account_filter=has_account_filter,
            has_merchant_filter=has_merchant_filter,
        ),
        calendar_day_json=build_calendar_day_json(days),
    )


def calendar_empty_state_message(
    has_applied_filters: bool,
    *,
    has_account_filter: bool = False,
    has_merchant_filter: bool = False,
) -> str:
    """Return the calendar empty-state message for the active filter context."""
    if has_account_filter and has_merchant_filter:
        return "No posted transactions match this account and merchant."
    if has_account_filter:
        return "No posted transactions match this account."
    if has_merchant_filter:
        return "No posted transactions match this merchant."
    if has_applied_filters:
        return "No posted transactions match the current filters."
    return "No posted transactions for this month."
