"""Read-side merchant query helpers.

Provides bounded merchant suggestion lookups for authenticated UI autocomplete
controls. Query callers pass SQLAlchemy Core connections and receive detached
mapping rows.
"""

from typing import Any

from sqlalchemy import and_, case, func, or_, select

from finance_app.database.tables import merchants as merchants_table
from finance_app.modules.merchants.filters import (
    merchant_search_term_groups,
    merchant_search_terms,
    parse_merchant_query,
)


def fetch_merchant_suggestions(conn: Any, query: object, limit: int) -> list[dict[str, Any]]:
    """Return known merchants matching partial query text."""
    terms = merchant_search_terms(query)
    if not terms or limit <= 0:
        return []

    merchant_key = func.lower(merchants_table.c.merchant_key)
    first_term = terms[0]
    term_groups = merchant_search_term_groups(query)
    rows = (
        conn.execute(
            select(
                merchants_table.c.id,
                merchants_table.c.merchant_key,
            )
            .where(
                or_(
                    *[
                        and_(*[merchant_key.contains(term, autoescape=True) for term in term_group])
                        for term_group in term_groups
                    ]
                )
            )
            .order_by(
                case(
                    (merchant_key == first_term, 0),
                    (merchant_key.like(f"{escape_like_prefix(first_term)}%", escape="\\"), 1),
                    else_=2,
                ),
                merchant_key,
                merchants_table.c.id,
            )
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )
    return [dict(row) for row in rows]


def merchant_suggestion_label(row: dict[str, Any]) -> str:
    """Return the user-facing label for one merchant suggestion row."""
    return str(row["merchant_key"])


def merchant_suggestion_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe merchant suggestion mapping."""
    label = merchant_suggestion_label(row)
    return {
        "id": int(row["id"]),
        "label": label,
        "value": label,
    }


def normalize_suggestion_query(value: object) -> str:
    """Normalize suggestion endpoint query text."""
    return parse_merchant_query(value)


def escape_like_prefix(value: object) -> str:
    """Escape SQL LIKE wildcard characters for a prefix match."""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
