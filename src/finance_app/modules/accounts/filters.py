"""Shared account filter helpers for transaction-backed feature modules.

Parses account query parameters and builds SQLAlchemy Core predicates for
features that report on transactions posted to a selected account.
"""

from typing import Any

from finance_app.database.tables import transactions as transactions_table


def parse_account_id(value: object) -> int | None:
    """Return a positive account id from a query value, or ``None`` for all accounts."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def account_filter_condition(account_id: object) -> Any | None:
    """Return a transaction account predicate for a selected account id."""
    parsed_account_id = parse_account_id(account_id)
    if parsed_account_id is None:
        return None
    return transactions_table.c.account_id == parsed_account_id
