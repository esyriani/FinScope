"""Application orchestration for the review feature."""

from collections.abc import Iterable
from typing import Any

from finance_app.background.runner import BackgroundJobSubmissionError, submit_background_job
from finance_app.core.config import settings
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.query import parse_page, parse_sort_direction
from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories.service import get_category_options, normalize_category
from finance_app.modules.categories.taxonomy import (
    get_category_description_map,
    get_tag_option_rows,
    get_tag_options,
    normalize_tag_names,
)
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
from finance_app.modules.review.queries import find_review_rule, review_candidate_rows, rule_snapshot
from finance_app.modules.review.repository import (
    review_candidates_with_tags,
    review_group_rows,
)
from finance_app.modules.review.urls import build_review_sort_url, build_review_url, parse_review_sort
from finance_app.modules.review.workflow import (
    apply_review_group_job,
    apply_review_group_transactions,
    rule_snapshots_match,
    save_review_rule,
    undo_review_group_job,
    undo_review_rule,
)
from finance_app.modules.rules.forms import normalize_rule_keyword, parse_amount_bounds
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category


def build_review_context(args: Any) -> dict[str, Any]:
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
    visible_rows = review_rows[offset : offset + page_size]
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


def parse_review_merchant_search(value: object) -> str:
    """Return a normalized merchant search string for review filters."""
    return " ".join(str(value or "").split())


def filter_review_groups_by_merchant(
    groups: list[dict[str, Any]],
    merchant_search: str,
) -> list[dict[str, Any]]:
    """Return review groups whose merchant key contains the search text."""
    if not merchant_search:
        return groups

    terms = [term.casefold() for term in merchant_search.split() if term]
    return [group for group in groups if all(term in str(group.get("merchant_key") or "").casefold() for term in terms)]


def review_groups(conn: Any, unknown_category: str, merchant_candidate: str = "") -> list[dict[str, Any]]:
    """Return review groups for candidate rows fetched from persistence."""
    rows, transaction_tags = review_candidates_with_tags(conn, unknown_category, merchant_candidate)
    return build_review_groups(rows, transaction_tags, unknown_category)


def queue_review_group_application(
    merchant_key_value: object,
    transaction_id_value: object,
    selected_transaction_values: Iterable[object],
    category_value: object,
    tag_values: Iterable[object],
    create_rule_value: object,
    keyword_value: object,
    amount_min_value: object,
    amount_max_value: object,
) -> dict[str, Any]:
    """Validate and queue a review group application workflow."""
    merchant_key = review_merchant_key(merchant_key_value)
    transaction_id = parse_review_transaction_id(transaction_id_value)
    selected_transaction_ids = (
        [] if transaction_id is not None else parse_review_transaction_ids(selected_transaction_values)
    )

    if not merchant_key:
        raise ValueError("Review group not found.")

    create_rule = str(create_rule_value or "") == "1" and not selected_transaction_ids
    with db_core_transaction() as conn:
        category_options = get_category_options(conn)
        tag_options = get_tag_options(conn)
        unknown_category = get_unknown_category(conn)
        category = normalize_category(category_value, category_options)
        tags = normalize_tag_names(tag_values, tag_options)
        group_transaction_ids = (
            {row["id"] for row in review_group_rows(conn, merchant_key, unknown_category)}
            if selected_transaction_ids
            else set()
        )

    if selected_transaction_ids and not set(selected_transaction_ids).issubset(group_transaction_ids):
        raise ValueError("Review transaction not found.")

    if (
        not category
        or category not in category_options
        or category == unknown_category
        or category.upper() == UNKNOWN_CATEGORY
    ):
        raise ValueError("Choose a category before applying the review group.")

    rule_keyword = ""
    amount_min = None
    amount_max = None
    if create_rule:
        rule_keyword = normalize_rule_keyword(keyword_value, merchant_key)
        amount_min, amount_max = parse_amount_bounds(amount_min_value, amount_max_value)
        if not rule_keyword:
            raise ValueError("Rule keyword is required when saving a rule.")

    undo_state: dict[str, Any] = {}
    job_label = (
        f"Review transaction {transaction_id} as {category}"
        if transaction_id
        else (
            f"Review {len(selected_transaction_ids)} transactions as {category}"
            if selected_transaction_ids
            else f"Review {short_label(merchant_key)} as {category}"
        )
    )
    job_kwargs: dict[str, Any] = {}
    if selected_transaction_ids:
        job_kwargs["selected_transaction_ids"] = selected_transaction_ids

    try:
        job_id = submit_background_job(
            job_label,
            apply_review_group_job,
            undo_state,
            merchant_key,
            category,
            tags,
            create_rule,
            rule_keyword,
            amount_min,
            amount_max,
            transaction_id,
            undo_handler=undo_review_group_job,
            undo_args=(undo_state,),
            **job_kwargs,
        )
    except BackgroundJobSubmissionError:
        return {
            "ok": False,
            "job_id": None,
            "target": "transaction" if transaction_id else "transactions" if selected_transaction_ids else "group",
            "message": "Review could not be queued. Try again.",
            "status": 503,
        }
    return {
        "ok": True,
        "job_id": job_id,
        "target": "transaction" if transaction_id else "transactions" if selected_transaction_ids else "group",
    }


def parse_review_transaction_id(value: object) -> int | None:
    """Parse an optional one-transaction review target from submitted data."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        transaction_id = int(text)
    except ValueError as exc:
        raise ValueError("Review transaction not found.") from exc

    if transaction_id <= 0:
        raise ValueError("Review transaction not found.")
    return transaction_id


def parse_review_transaction_ids(values: Iterable[object]) -> list[int]:
    """Parse selected review transaction ids from submitted form values."""
    transaction_ids: list[int] = []
    seen: set[int] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            transaction_id = int(text)
        except ValueError as exc:
            raise ValueError("Review transaction not found.") from exc

        if transaction_id <= 0:
            raise ValueError("Review transaction not found.")
        if transaction_id in seen:
            continue
        transaction_ids.append(transaction_id)
        seen.add(transaction_id)
    return transaction_ids


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
    "parse_review_transaction_id",
    "parse_review_transaction_ids",
    "queue_review_group_application",
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
