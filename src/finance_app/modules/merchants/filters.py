"""Merchant filter parsing and SQL predicates.

Shared analytics filters use durable merchant IDs for selected suggestions and
portable partial-text predicates for free-text merchant searches.
"""

from typing import Any

from sqlalchemy import and_, exists, func, or_, select

from finance_app.database.tables import merchants as merchants_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.merchants.normalization import clean_merchant_description
from finance_app.modules.merchants.sql_filters import description_contains_candidate


def parse_merchant_id(value: object) -> int | None:
    """Return a positive merchant ID from query/form input."""
    try:
        merchant_id = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return merchant_id if merchant_id > 0 else None


def parse_merchant_query(value: object) -> str:
    """Return normalized free-text merchant query input."""
    return " ".join(str(value or "").strip().split())


def merchant_filter_condition(merchant_id: int | None, merchant_query: object = "") -> Any | None:
    """Return a transaction merchant predicate for exact or partial filtering."""
    if merchant_id is not None:
        return transactions_table.c.merchant_id == merchant_id
    return merchant_partial_match_condition(merchant_query)


def merchant_partial_match_condition(value: object) -> Any | None:
    """Return a partial merchant predicate for known merchants and descriptions."""
    terms = merchant_search_terms(value)
    if not terms:
        return None

    conditions: list[Any] = []
    merchant_key = func.lower(merchants_table.c.merchant_key)
    description = func.lower(transactions_table.c.description)

    for term_group in merchant_search_term_groups(value):
        conditions.append(and_(*[description.contains(term, autoescape=True) for term in term_group]))
        conditions.append(
            exists(
                select(1)
                .select_from(merchants_table)
                .where(
                    merchants_table.c.id == transactions_table.c.merchant_id,
                    *[merchant_key.contains(term, autoescape=True) for term in term_group],
                )
                .correlate(transactions_table)
            )
        )

    normalized = clean_merchant_description(value).cleaned_key
    normalized_description_condition = description_contains_candidate(transactions_table.c.description, normalized)
    if normalized_description_condition is not None:
        conditions.append(normalized_description_condition)

    return or_(*conditions)


def merchant_search_terms(value: object) -> list[str]:
    """Return lower-case search terms for raw and normalized merchant text."""
    raw = parse_merchant_query(value)
    normalized = clean_merchant_description(raw).cleaned_key
    terms: list[str] = []
    for term in (raw, normalized):
        normalized_term = term.casefold()
        if normalized_term and normalized_term not in terms:
            terms.append(normalized_term)
    return terms


def merchant_search_term_groups(value: object) -> list[list[str]]:
    """Return token groups where every token in a group must match."""
    groups: list[list[str]] = []
    for term in merchant_search_terms(value):
        tokens = [token for token in term.split() if token]
        if tokens and tokens not in groups:
            groups.append(tokens)
    return groups
