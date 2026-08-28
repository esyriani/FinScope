"""Automatic rule persistence helpers for accepted LLM categorizations.

LLM result validation decides whether a no-review result is eligible for a
future matching rule. This module owns the database write details for that
rule so the main LLM adapter can keep provider/result responsibilities focused.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_CREDIT,
    CATEGORY_RULE_DIRECTION_DEBIT,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
)
from finance_app.core.money import MoneyValue, optional_money_to_decimal
from finance_app.modules.categories.repository import save_category_rule
from finance_app.modules.merchants.normalization import normalize_merchant_description


def automatic_rule_amount_bounds(amount: MoneyValue | None) -> tuple[Decimal | None, Decimal | None]:
    """Return signed amount bounds for an automatically created rule.

    Automatic categorization deduplicates candidate transactions by merchant and
    amount direction. Persisting the same direction boundary on the generated
    rule keeps future rule matches aligned with that decision scope.
    """
    if amount is None:
        return None, None

    value = optional_money_to_decimal(amount)
    if value is None:
        return None, None

    if value < 0:
        return None, Decimal("0.00")
    return Decimal("0.00"), None


def save_automatic_category_rule(
    conn: Any,
    transaction: Mapping[str, Any],
    category: str,
    tags: Sequence[str],
) -> int | None:
    """Persist an accepted no-review LLM categorization as an automatic rule."""
    keyword = normalize_merchant_description(transaction.get("merchant_key") or transaction.get("description") or "")
    if not keyword:
        return None

    amount_min, amount_max = automatic_rule_amount_bounds(transaction.get("amount"))
    return save_category_rule(
        conn,
        keyword,
        category,
        source=CATEGORY_RULE_SOURCE_AUTOMATIC,
        amount_min=amount_min,
        amount_max=amount_max,
        tags=tags,
        merchant_id=transaction.get("merchant_id"),
        account_id=transaction.get("account_id"),
        direction=automatic_rule_direction(transaction.get("amount")),
        protect_user_rule=True,
    )


def automatic_rule_direction(amount: MoneyValue | None) -> str:
    """Return the signed direction constraint for an automatic LLM rule."""
    amount = optional_money_to_decimal(amount)
    if amount is None:
        return CATEGORY_RULE_DIRECTION_ANY
    return CATEGORY_RULE_DIRECTION_CREDIT if amount < 0 else CATEGORY_RULE_DIRECTION_DEBIT
