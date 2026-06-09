"""URL builders for the review feature."""

from collections.abc import Iterable
from urllib.parse import urlencode

from flask import url_for


def build_review_url(
    page: int, ungrouped_keys: Iterable[str], sort: str, direction: str, merchant_search: str = ""
) -> str:
    """Build review URL."""
    query = [("page", page), ("sort", sort), ("direction", direction)]
    if merchant_search:
        query.append(("merchant", merchant_search))
    query.extend(("ungroup", key) for key in ungrouped_keys)
    return f"{url_for('review.review')}?{urlencode(query)}"


def build_review_sort_url(
    next_sort: object,
    current_sort: str,
    current_direction: str,
    ungrouped_keys: Iterable[str],
    merchant_search: str = "",
) -> str:
    """Build review sort URL."""
    next_sort = parse_review_sort(next_sort)
    if next_sort == current_sort:
        next_direction = "desc" if current_direction == "asc" else "asc"
    else:
        next_direction = "desc" if next_sort == "review_set" else "asc"

    return build_review_url(1, ungrouped_keys, next_sort, next_direction, merchant_search)


def parse_review_sort(value: object) -> str:
    """Parse review sort."""
    sort = str(value or "review_set").strip()
    if sort not in {"merchant", "review_set"}:
        return "review_set"
    return sort
