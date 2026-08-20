"""Recurring activity read-model orchestration.

Builds the shared recurring activity context used by the recurring page, the
calendar page, and the Home page. It owns recurrence source queries, pattern
metadata, merchant filtering, and JSON-safe payload assembly.
"""

from calendar import monthrange
from datetime import date
from typing import Any

from finance_app.core.config import settings
from finance_app.core.constants import UNKNOWN_CATEGORY
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
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category

from .parsing import default_month
from .patterns import get_recurring_pattern_metadata
from .presenter import (
    build_recurring_activity_json,
    build_recurring_activity_summary,
    build_recurring_transaction_rows,
)
from .queries import (
    build_category_filter,
    fetch_month_transactions,
    fetch_recurring_source_rows,
    get_recurrence_detection_settings,
)
from .recurrence import infer_recurring_items


def build_recurring_activity_context(
    selected_month: date | None = None,
    selected_categories: list[str] | None = None,
    selected_tags: list[str] | None = None,
    selected_account_id: int | None = None,
    selected_merchant_id: int | None = None,
    merchant_query: str = "",
) -> dict[str, Any]:
    """Build recurring activity rows, metadata, and serialized payloads."""
    selected_month = selected_month or default_month()
    selected_categories = selected_categories or []
    selected_tags = selected_tags or []
    selected_account_id = parse_account_id(selected_account_id)
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
        recurrence_transactions = build_recurring_transaction_rows(
            recurrence_month_rows,
            conn,
            account_id=selected_account_id,
            merchant_search=transaction_merchant_search,
        )
        transactions = (
            build_recurring_transaction_rows(
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
        "summary": build_recurring_activity_summary(transactions, recurring_items),
        "recurring_activity_json": build_recurring_activity_json(recurring_items),
    }


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
