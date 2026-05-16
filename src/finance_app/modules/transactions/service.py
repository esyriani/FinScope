"""Application orchestration for the transactions feature."""

from finance_app.core.config import settings
from finance_app.core.periods import DATE_PERIOD_OPTIONS, PERIOD_CUSTOM
from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.taxonomy import (
    get_category_description_map,
    get_tag_color_map,
    get_tag_option_rows,
    get_transaction_tags_by_id,
)
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category
from finance_app.modules.transactions.constants import (
    CATEGORY_SOURCE_FILTER_OPTIONS,
    IGNORED_FILTER_OPTIONS,
    REVIEW_FILTER_OPTIONS,
)
from finance_app.modules.transactions.filters import (
    build_transaction_core_filters,
    parse_transaction_filters,
    transaction_sort,
)
from finance_app.modules.transactions.presenter import build_transaction_rows
from finance_app.modules.transactions.queries import count_transactions, fetch_distinct_categories, fetch_transactions
from finance_app.modules.transactions.urls import transactions_sort_url, transactions_url


def build_transactions_context(args):
    """Build transactions context."""
    with db_core_transaction() as conn:
        filters = parse_transaction_filters(args, conn)
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        unknown_category = get_unknown_category(conn)
        sort, sort_expression = transaction_sort(filters, unknown_category)
        core_filters = build_transaction_core_filters(filters, unknown_category, conn=conn)
        filter_criteria = core_filters.criteria()
        total_count = count_transactions(conn, filter_criteria)
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(filters["page"], total_pages)
        offset = (page - 1) * page_size

        fetched_rows = fetch_transactions(
            conn,
            filter_criteria,
            sort_expression,
            filters["direction"],
            page_size,
            offset,
        )
        tag_map = get_transaction_tags_by_id(conn, [row["id"] for row in fetched_rows])
        rows = build_transaction_rows(fetched_rows, tag_map, get_tag_color_map(conn), conn)
        categories = fetch_distinct_categories(conn)
        category_options = get_category_options(conn)
        category_descriptions = get_category_description_map(conn)
        tag_display_options = get_tag_option_rows(conn)

    return {
        "transactions": rows,
        "categories": categories,
        "search": filters["search"],
        "selected_category": filters["category"],
        "selected_tags": filters["selected_tags"],
        "selected_review": filters["review"],
        "selected_category_source": filters["category_source"],
        "selected_ignored": filters["ignored"],
        "selected_period": filters["period"],
        "period_options": DATE_PERIOD_OPTIONS,
        "period_custom": PERIOD_CUSTOM,
        "review_filter_options": REVIEW_FILTER_OPTIONS,
        "category_source_filter_options": CATEGORY_SOURCE_FILTER_OPTIONS,
        "ignored_filter_options": IGNORED_FILTER_OPTIONS,
        "sort": sort,
        "direction": filters["direction"],
        "page_url": lambda page_number: transactions_url(page=page_number),
        "sort_url": lambda sort_name: transactions_sort_url(sort_name, sort, filters["direction"]),
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "page_start": offset + 1 if total_count else 0,
        "page_end": min(offset + page_size, total_count),
        "category_options": category_options,
        "category_descriptions": category_descriptions,
        "tag_options": tag_display_options,
    }
