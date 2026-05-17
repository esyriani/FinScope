"""Filter parsing helpers for the dashboard feature."""

from sqlalchemy import func

from finance_app.core.constants import FILTER_MODE_INCLUDE
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.tag_filters import transaction_tag_condition

from .constants import (
    DASHBOARD_CATEGORY_SORT_CATEGORY,
    DASHBOARD_BREAKDOWN_CATEGORY,
    DASHBOARD_BREAKDOWN_OPTIONS,
    DASHBOARD_MERCHANT_SORT_CATEGORY,
    DASHBOARD_MERCHANT_SORT_MERCHANT,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_CUSTOM,
    QUICK_VIEW_NEEDS_REVIEW,
    QUICK_VIEW_OPTIONS,
    QUICK_VIEW_UNKNOWN,
)


def parse_dashboard_table_sort(value, allowed_sorts, default):
    """Parse dashboard table sort."""
    sort = str(value or default).strip()
    return sort if sort in allowed_sorts else default


def dashboard_table_default_direction(sort):
    """Render table default direction."""
    text_sorts = {
        DASHBOARD_MERCHANT_SORT_MERCHANT,
        DASHBOARD_MERCHANT_SORT_CATEGORY,
        DASHBOARD_CATEGORY_SORT_CATEGORY,
    }
    return "asc" if sort in text_sorts else "desc"


def parse_dashboard_breakdown(value):
    """Parse the dashboard breakdown mode."""
    breakdown = str(value or "").strip()
    return breakdown if breakdown in DASHBOARD_BREAKDOWN_OPTIONS else DASHBOARD_BREAKDOWN_CATEGORY


def parse_dashboard_flag(value):
    """Parse an optional dashboard boolean query flag."""
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def parse_quick_view(value, selected_categories, selected_tags=None):
    """Parse quick view."""
    quick_view = str(value or "").strip()
    if quick_view in QUICK_VIEW_OPTIONS:
        return quick_view
    if selected_categories or selected_tags:
        return QUICK_VIEW_CUSTOM
    return QUICK_VIEW_CATEGORIZED


def apply_quick_view_core_filter(
    filters,
    quick_view,
    selected_categories,
    selected_tags,
    filter_mode,
    unknown_category,
):
    """Apply a quick-view filter to SQLAlchemy Core filters."""
    category_value = func.coalesce(transactions_table.c.category, unknown_category)
    include = filter_mode == FILTER_MODE_INCLUDE

    if quick_view == QUICK_VIEW_NEEDS_REVIEW:
        filters.add(transactions_table.c.needs_review == 1)
    elif quick_view == QUICK_VIEW_UNKNOWN:
        filters.add(category_value == unknown_category)
    elif quick_view == QUICK_VIEW_CATEGORIZED:
        filters.add(category_value != unknown_category)
        filters.add(transactions_table.c.needs_review == 0)
    elif quick_view == QUICK_VIEW_CUSTOM:
        filters.add_in(category_value, selected_categories, include=include)
        filters.add(transaction_tag_condition(selected_tags or [], include=include))
