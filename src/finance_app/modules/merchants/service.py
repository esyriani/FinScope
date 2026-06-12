"""Merchant autocomplete service helpers.

Builds bounded JSON payloads for merchant lookup UI while keeping controllers
free of SQL and response-shaping details.
"""

from typing import Any

from finance_app.core.config import settings
from finance_app.database.engine import db_core_transaction
from finance_app.modules.merchants.queries import (
    fetch_merchant_suggestions,
    merchant_suggestion_payload,
    normalize_suggestion_query,
)
from finance_app.modules.merchants.repository import find_merchant_by_id
from finance_app.modules.settings.runtime import get_int_setting

MAX_MERCHANT_SUGGESTION_LIMIT = 20


def build_merchant_suggestions_payload(args: Any) -> dict[str, Any]:
    """Return merchant suggestions for request query parameters."""
    query = normalize_suggestion_query(args.get("q"))
    with db_core_transaction() as conn:
        configured_limit = get_merchant_suggestion_limit(conn)
        limit = parse_suggestion_limit(args.get("limit"), configured_limit)
        suggestions = [
            merchant_suggestion_payload(row)
            for row in fetch_merchant_suggestions(
                conn,
                query,
                limit,
            )
        ]

    return {
        "ok": True,
        "query": query,
        "suggestions": suggestions,
    }


def get_merchant_suggestion_limit(conn: Any) -> int:
    """Return the configured maximum merchant autocomplete result count."""
    configured_limit = get_int_setting(
        conn,
        "merchant_suggestion_limit",
        settings.default_merchant_suggestion_limit,
    )
    return max(1, min(configured_limit, MAX_MERCHANT_SUGGESTION_LIMIT))


def parse_suggestion_limit(value: object, configured_limit: int) -> int:
    """Return a bounded merchant suggestion result limit."""
    try:
        limit = int(str(value or "").strip())
    except (TypeError, ValueError):
        limit = configured_limit
    return max(1, min(limit, configured_limit, MAX_MERCHANT_SUGGESTION_LIMIT))


def selected_merchant_filter_label(conn: Any, selected_merchant_id: int | None, merchant_query: str = "") -> str:
    """Return the display label for an analytics merchant filter."""
    if selected_merchant_id is None:
        return merchant_query
    merchant = find_merchant_by_id(conn, selected_merchant_id)
    if merchant is None:
        return merchant_query
    return str(merchant["merchant_key"])
