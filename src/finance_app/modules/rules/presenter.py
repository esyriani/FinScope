"""Presentation helpers for rule-management responses.

This module shapes already-loaded rule workflow data for templates or JSON
responses. It does not query or mutate persistence.
"""

from collections.abc import Mapping
from typing import Any

from finance_app.core.filters import format_money
from finance_app.core.money import money_to_float


def present_rule_preview_transaction(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready transaction row for the rule preview modal."""
    amount = money_to_float(row["amount"])
    return {
        "id": row["id"],
        "tx_date": row["tx_date"],
        "description": row["description"],
        "amount": amount,
        "amount_display": format_money(amount),
        "current_category": row["category"] or "",
    }
