"""Application orchestration for the calendar feature."""

from calendar import monthrange
from datetime import date
from typing import Any

from finance_app.core.config import settings
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.i18n import format_month_year, weekday_abbreviation_labels
from finance_app.database.engine import db_core_transaction
from finance_app.modules.accounts.filters import parse_account_id
from finance_app.modules.accounts.queries import list_account_options
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.taxonomy import get_tag_option_rows
from finance_app.modules.merchants.filters import (
    merchant_search_term_groups,
    parse_merchant_id,
    parse_merchant_query,
)
from finance_app.modules.merchants.normalization import clean_merchant_description
from finance_app.modules.merchants.service import get_merchant_suggestion_limit, selected_merchant_filter_label
from finance_app.modules.recurring.patterns import get_recurring_pattern_metadata
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category

from .parsing import clean_categories, clean_tags, default_month, parse_heatmap_metric, parse_month, shift_month
from .presenter import (
    apply_heatmap,
    build_calendar_day_json,
    build_calendar_days,
    build_calendar_summary,
    build_calendar_transactions,
    build_recurring_activity_json,
)
from .queries import (
    build_category_filter,
    fetch_month_transactions,
    fetch_recurring_source_rows,
    get_recurrence_detection_settings,
)
from .recurrence import infer_recurring_items
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


def build_recurring_activity_context(
    selected_month: date | None = None,
    selected_categories: list[str] | None = None,
    selected_tags: list[str] | None = None,
    selected_account_id: int | None = None,
    selected_merchant_id: int | None = None,
    merchant_query: str = "",
) -> dict[str, Any]:
    """Build recurring activity context."""
    selected_month = selected_month or default_month()
    selected_categories = selected_categories or []
    selected_tags = selected_tags or []
    selected_merchant_id = parse_merchant_id(selected_merchant_id)
    merchant_query = parse_merchant_query(merchant_query)
    month_start = selected_month.replace(day=1)
    last_day = monthrange(selected_month.year, selected_month.month)[1]
    month_end = selected_month.replace(day=last_day)

    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        category_options = get_category_options(conn)
        tag_options = get_tag_option_rows(conn)
        account_options = list_account_options(conn)
        category_filter = build_category_filter(
            selected_categories,
            selected_tags,
            unknown_category,
            selected_account_id,
        )
        transaction_filter = build_category_filter(
            selected_categories,
            selected_tags,
            unknown_category,
            selected_account_id,
            selected_merchant_id,
            merchant_query,
        )
        recurrence_settings = get_recurrence_detection_settings(conn)
        table_page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        merchant_suggestion_limit = get_merchant_suggestion_limit(conn)
        selected_merchant_label = selected_merchant_filter_label(conn, selected_merchant_id, merchant_query)
        transaction_merchant_search = selected_merchant_label if selected_merchant_id else merchant_query
        recurring_pattern_metadata = get_recurring_pattern_metadata(conn)
        recurrence_month_rows = fetch_month_transactions(
            conn, month_start, month_end, unknown_category, category_filter
        )
        month_rows = (
            fetch_month_transactions(conn, month_start, month_end, unknown_category, transaction_filter)
            if selected_merchant_id or merchant_query
            else recurrence_month_rows
        )
        recurring_rows = fetch_recurring_source_rows(conn, month_start, unknown_category, category_filter)
        recurrence_transactions = build_calendar_transactions(
            recurrence_month_rows,
            conn,
            account_id=selected_account_id,
            merchant_search=transaction_merchant_search,
        )
        transactions = (
            build_calendar_transactions(
                month_rows,
                conn,
                account_id=selected_account_id,
                merchant_search=transaction_merchant_search,
            )
            if selected_merchant_id or merchant_query
            else recurrence_transactions
        )
        recurring_items = infer_recurring_items(
            recurring_rows,
            month_start,
            month_end,
            recurrence_transactions,
            recurrence_settings,
            recurring_pattern_metadata,
            conn=conn,
            account_id=selected_account_id,
            merchant_search=transaction_merchant_search,
        )
        recurring_items = filter_recurring_items_by_merchant(
            recurring_items,
            selected_merchant_id,
            merchant_query,
        )
    return {
        "month_start": month_start,
        "month_end": month_end,
        "category_options": category_options,
        "tag_options": tag_options,
        "account_options": account_options,
        "selected_merchant_label": selected_merchant_label,
        "merchant_suggestion_limit": merchant_suggestion_limit,
        "transactions": transactions,
        "recurring_items": recurring_items,
        "table_page_size": table_page_size,
        "summary": build_calendar_summary(transactions, recurring_items),
        "recurring_activity_json": build_recurring_activity_json(recurring_items),
    }


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


def filter_recurring_items_by_merchant(
    recurring_items: list[dict[str, Any]],
    selected_merchant_id: int | None,
    merchant_query: str = "",
) -> list[dict[str, Any]]:
    """Return recurring items matching an exact merchant or partial text."""
    if selected_merchant_id is not None:
        return [
            item
            for item in recurring_items
            if selected_merchant_id
            in {
                parse_merchant_id(item.get("merchant_id")),
                parse_merchant_id(item.get("pattern_merchant_id")),
            }
        ]
    if not merchant_query:
        return recurring_items
    return [item for item in recurring_items if recurring_item_matches_merchant_query(item, merchant_query)]


def recurring_item_matches_merchant_query(item: dict[str, Any], merchant_query: str) -> bool:
    """Return whether a recurring item matches merchant text or example rows."""
    texts = [
        item.get("merchant"),
        item.get("pattern_merchant"),
        item.get("pattern_key"),
    ]
    texts.extend(occurrence.get("description") for occurrence in item.get("occurrences", []) if occurrence)
    return any(merchant_text_matches_query(text, merchant_query) for text in texts)


def merchant_text_matches_query(text: object, merchant_query: str) -> bool:
    """Return whether one display or normalized text value matches merchant query terms."""
    values = [str(text or "").casefold()]
    normalized_text = clean_merchant_description(text).cleaned_key.casefold()
    if normalized_text and normalized_text not in values:
        values.append(normalized_text)
    for term_group in merchant_search_term_groups(merchant_query):
        if any(all(term in value for term in term_group) for value in values):
            return True
    return False
