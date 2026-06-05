"""Application orchestration for the review feature."""

from finance_app.core.config import settings
from finance_app.core.query import parse_page, parse_sort_direction
from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.taxonomy import get_category_description_map, get_tag_option_rows
from finance_app.modules.review.normalization import review_merchant_key
from finance_app.modules.review.presenter import (
    active_ungroup_keys,
    attach_review_row_urls,
    build_review_groups,
    is_unknown_category,
    review_display_rows,
    review_group_default_sort_key,
    review_group_display_row,
    review_summary,
    review_transaction_display_row,
    selected_ungroup_keys,
    short_label,
    sort_review_groups,
    sortable_text,
    with_ungroup_key,
    without_ungroup_key,
)
from finance_app.modules.review.repository import (
    review_candidates_with_tags,
    review_group_rows,
)
from finance_app.modules.review.queries import find_review_rule, review_candidate_rows, rule_snapshot
from finance_app.modules.review.urls import build_review_sort_url, build_review_url, parse_review_sort
from finance_app.modules.review.workflow import (
    apply_review_group_job,
    apply_review_group_transactions,
    rule_snapshots_match,
    save_review_rule,
    undo_review_group_job,
    undo_review_rule,
)
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category


def build_review_context(args):
    """Build review context."""
    page = parse_page(args.get("page"))
    sort = parse_review_sort(args.get("sort"))
    direction = parse_sort_direction(args.get("direction"), default="desc")
    merchant_search = parse_review_merchant_search(args.get("merchant"))
    ungrouped_keys = selected_ungroup_keys(args.getlist("ungroup"))
    with db_core_transaction() as conn:
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        unknown_category = get_unknown_category(conn)
        category_options = get_category_options(conn)
        category_descriptions = get_category_description_map(conn)
        tag_options = get_tag_option_rows(conn)
        groups = review_groups(conn, unknown_category, merchant_search)
        groups = filter_review_groups_by_merchant(groups, merchant_search)
        summary = review_summary(groups)
        ungrouped_keys = active_ungroup_keys(ungrouped_keys, groups)
        sort_review_groups(groups, sort, direction)
        review_rows = review_display_rows(groups, ungrouped_keys, unknown_category)

    total_count = len(review_rows)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(page, total_pages)
    offset = (page - 1) * page_size
    visible_rows = review_rows[offset:offset + page_size]
    attach_review_row_urls(visible_rows, page, ungrouped_keys, sort, direction, merchant_search)

    return dict(
        groups=visible_rows,
        category_options=category_options,
        category_descriptions=category_descriptions,
        tag_options=tag_options,
        unknown_category=unknown_category,
        summary=summary,
        merchant_search=merchant_search,
        ungrouped_keys=ungrouped_keys,
        sort=sort,
        direction=direction,
        page_url=lambda page_number: build_review_url(
            page_number,
            ungrouped_keys,
            sort,
            direction,
            merchant_search,
        ),
        sort_url=lambda sort_name: build_review_sort_url(
            sort_name,
            sort,
            direction,
            ungrouped_keys,
            merchant_search,
        ),
        page=page,
        page_size=page_size,
        total_count=total_count,
        total_pages=total_pages,
        page_start=offset + 1 if total_count else 0,
        page_end=min(offset + page_size, total_count),
    )


def parse_review_merchant_search(value):
    """Return a normalized merchant search string for review filters."""
    return " ".join(str(value or "").split())


def filter_review_groups_by_merchant(groups, merchant_search):
    """Return review groups whose merchant key contains the search text."""
    if not merchant_search:
        return groups

    needle = merchant_search.casefold()
    return [
        group
        for group in groups
        if needle in str(group.get("merchant_key") or "").casefold()
    ]


def review_groups(conn, unknown_category, merchant_candidate=""):
    """Return review groups for candidate rows fetched from persistence."""
    rows, transaction_tags = review_candidates_with_tags(conn, unknown_category, merchant_candidate)
    return build_review_groups(rows, transaction_tags, unknown_category)


__all__ = [
    "active_ungroup_keys",
    "apply_review_group_job",
    "apply_review_group_transactions",
    "attach_review_row_urls",
    "build_review_context",
    "build_review_sort_url",
    "build_review_url",
    "find_review_rule",
    "filter_review_groups_by_merchant",
    "is_unknown_category",
    "parse_review_sort",
    "parse_review_merchant_search",
    "review_candidate_rows",
    "review_display_rows",
    "review_group_default_sort_key",
    "review_group_display_row",
    "review_group_rows",
    "review_groups",
    "review_merchant_key",
    "review_summary",
    "review_transaction_display_row",
    "rule_snapshot",
    "rule_snapshots_match",
    "save_review_rule",
    "selected_ungroup_keys",
    "short_label",
    "sort_review_groups",
    "sortable_text",
    "undo_review_group_job",
    "undo_review_rule",
    "with_ungroup_key",
    "without_ungroup_key",
]
