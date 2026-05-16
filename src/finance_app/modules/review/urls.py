"""URL builders for the review feature."""

from urllib.parse import urlencode

from flask import url_for


def build_review_url(page, ungrouped_keys, sort, direction, merchant_search=""):
    """Build review URL."""
    query = [("page", page), ("sort", sort), ("direction", direction)]
    if merchant_search:
        query.append(("merchant", merchant_search))
    query.extend(("ungroup", key) for key in ungrouped_keys)
    return f"{url_for('review.review')}?{urlencode(query)}"


def build_review_sort_url(next_sort, current_sort, current_direction, ungrouped_keys, merchant_search=""):
    """Build review sort URL."""
    next_sort = parse_review_sort(next_sort)
    if next_sort == current_sort:
        next_direction = "desc" if current_direction == "asc" else "asc"
    else:
        next_direction = "desc" if next_sort == "review_set" else "asc"

    return build_review_url(1, ungrouped_keys, next_sort, next_direction, merchant_search)


def parse_review_sort(value):
    """Parse review sort."""
    sort = str(value or "review_set").strip()
    if sort not in {"merchant", "review_set"}:
        return "review_set"
    return sort
