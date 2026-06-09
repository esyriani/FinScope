"""SQLAlchemy Core merchant matching predicates.

Provides reusable SQL candidate filters for features that still need Python
merchant normalization for exact matching. The helpers push simple ID and
token predicates into SQL so callers do not scan every transaction row.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import and_, false, func, or_, select

from finance_app.database.tables import merchants as merchants_table
from finance_app.modules.merchants.normalization import clean_merchant_description


def merchant_identity_candidates(conn: Any | None, merchant_key: object) -> tuple[set[int], set[str]]:
    """Return merchant IDs and description keys that may match a merchant."""
    normalized_key = clean_merchant_description(merchant_key).cleaned_key
    merchant_ids: set[int] = set()
    description_candidates: set[str] = set()
    add_description_candidate(description_candidates, normalized_key)

    if conn is None or not normalized_key:
        return merchant_ids, description_candidates

    rows = (
        conn.execute(
            select(
                merchants_table.c.id,
                merchants_table.c.merchant_key,
            ).where(
                func.lower(merchants_table.c.merchant_key) == normalized_key.lower(),
            )
        )
        .mappings()
        .fetchall()
    )

    for row in rows:
        merchant_ids.add(row["id"])
        add_description_candidate(description_candidates, row["merchant_key"])

    return merchant_ids, description_candidates


def merchant_description_candidates(conn: Any | None, merchant_key: object) -> set[str]:
    """Return normalized description keys that may match a merchant key."""
    return merchant_identity_candidates(conn, merchant_key)[1]


def add_description_candidate(candidates: set[str], value: object) -> None:
    """Add one cleaned description candidate when it is present."""
    cleaned = clean_merchant_description(value).cleaned_key
    if cleaned:
        candidates.add(cleaned)


def description_matches_any_candidate(column: Any, candidates: Iterable[str]) -> Any:
    """Return a SQL predicate matching any normalized description candidate."""
    conditions: list[Any] = []
    for candidate in sorted(candidates):
        condition = description_contains_candidate(column, candidate)
        if condition is not None:
            conditions.append(condition)

    if not conditions:
        return false()
    return or_(*conditions)


def description_contains_candidate(column: Any, candidate: object) -> Any | None:
    """Return a SQL predicate for candidate tokens appearing in a description.

    Exact merchant normalization still happens in Python. This predicate is
    intentionally a coarse candidate filter so the database can discard obvious
    non-matches before Python applies full normalization rules.
    """
    tokens = clean_merchant_description(candidate).cleaned_key.split()
    if not tokens:
        return None

    upper_description = func.upper(column)
    return and_(*[upper_description.like(f"%{escape_like_token(token)}%", escape="\\") for token in tokens])


def escape_like_token(token: object) -> str:
    """Escape wildcard characters in a SQL LIKE token."""
    return str(token).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
