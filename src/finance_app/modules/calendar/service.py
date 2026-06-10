"""Application orchestration for the calendar feature."""

from calendar import monthrange
from datetime import date
from typing import Any

from finance_app.core.config import settings
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.i18n import format_month_year, weekday_abbreviation_labels
from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.taxonomy import get_tag_option_rows
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
from .urls import calendar_url, transactions_url


def build_calendar_context(args: Any) -> dict[str, Any]:
    """Build calendar context."""
    selected_month = parse_month(args.get("month")) or default_month()
    heatmap_metric = parse_heatmap_metric(args.get("heatmap"))
    selected_categories = clean_categories(args.getlist("categories"))
    selected_tags = clean_tags(args.getlist("tags"))
    recurring_context = build_recurring_activity_context(selected_month, selected_categories, selected_tags)
    month_start = recurring_context["month_start"]
    month_end = recurring_context["month_end"]
    days = build_calendar_days(month_start, recurring_context["transactions"])
    apply_heatmap(days, heatmap_metric)
    navigation_params = {
        "heatmap": heatmap_metric,
        "categories": selected_categories,
        "tags": selected_tags,
    }

    return dict(
        selected_month=month_start.isoformat()[:7],
        selected_categories=selected_categories,
        selected_tags=selected_tags,
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
        month_transactions_url=transactions_url(month_start.isoformat(), month_end.isoformat()),
        calendar_day_json=build_calendar_day_json(days),
    )


def build_recurring_activity_context(
    selected_month: date | None = None,
    selected_categories: list[str] | None = None,
    selected_tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build recurring activity context."""
    selected_month = selected_month or default_month()
    selected_categories = selected_categories or []
    selected_tags = selected_tags or []
    month_start = selected_month.replace(day=1)
    last_day = monthrange(selected_month.year, selected_month.month)[1]
    month_end = selected_month.replace(day=last_day)

    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        category_options = get_category_options(conn)
        tag_options = get_tag_option_rows(conn)
        category_filter = build_category_filter(selected_categories, selected_tags, unknown_category)
        recurrence_settings = get_recurrence_detection_settings(conn)
        table_page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        recurring_pattern_metadata = get_recurring_pattern_metadata(conn)
        month_rows = fetch_month_transactions(conn, month_start, month_end, unknown_category, category_filter)
        recurring_rows = fetch_recurring_source_rows(conn, month_start, unknown_category, category_filter)
        transactions = build_calendar_transactions(month_rows, conn)
        recurring_items = infer_recurring_items(
            recurring_rows,
            month_start,
            month_end,
            transactions,
            recurrence_settings,
            recurring_pattern_metadata,
            conn=conn,
        )
    return {
        "month_start": month_start,
        "month_end": month_end,
        "category_options": category_options,
        "tag_options": tag_options,
        "transactions": transactions,
        "recurring_items": recurring_items,
        "table_page_size": table_page_size,
        "summary": build_calendar_summary(transactions, recurring_items),
        "recurring_activity_json": build_recurring_activity_json(recurring_items),
    }
