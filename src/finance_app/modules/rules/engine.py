"""Pure domain engine for category-rule semantics.

The helpers in this module decide whether rules match transactions and build
rule-assignment metadata. They do not open database connections, construct SQL,
persist state, queue jobs, or shape display payloads.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from finance_app.core.builtin_taxonomy import is_income_category_name
from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_CREDIT,
    CATEGORY_RULE_DIRECTION_DEBIT,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
    TRANSFER_CATEGORY,
)
from finance_app.core.money import MoneyValue, money_to_decimal, money_to_float, rounded_money_decimal
from finance_app.modules.categories.decision import DECISION_SOURCE_RULE
from finance_app.modules.categories.rules_matching import merchant_match_candidates, rule_amount_matches
from finance_app.modules.merchants.normalization import normalize_merchant, normalize_merchant_description


def rule_preview_matches_transaction(
    rule: Mapping[str, Any],
    transaction: Mapping[str, Any],
    keyword: str,
) -> bool:
    """Return whether a transaction matches the current preview filter."""
    if not rule_account_matches_transaction(rule, transaction):
        return False
    if not rule_direction_matches_transaction(rule, transaction["amount"]):
        return False

    rule_merchant_id = mapping_value(rule, "merchant_id")
    if rule_merchant_id:
        transaction_merchant_id = mapping_value(transaction, "merchant_id")
        if transaction_merchant_id is None or int(transaction_merchant_id) != int(rule_merchant_id):
            return False
    else:
        normalized_merchant = normalize_merchant(transaction["description"])
        candidates = merchant_match_candidates(
            normalized_merchant.merchant_key,
            normalized_merchant.merchant_key,
            raw_description=transaction["description"],
        )
        if not any(keyword in candidate for candidate in candidates):
            return False

    amount = rounded_money_decimal(transaction["amount"])
    if is_income_category_name(rule["category"]) and amount >= 0:
        return False

    return rule_amount_matches(rule, amount)


def rule_matches_transaction(rule: Mapping[str, Any], transaction: Mapping[str, Any]) -> bool:
    """Return whether one rule matches one transaction under apply semantics."""
    amount = rounded_money_decimal(transaction["amount"])
    if is_income_category_name(rule["category"]) and amount >= 0:
        return False
    if not rule_account_matches_transaction(rule, transaction):
        return False
    if not rule_direction_matches_transaction(rule, amount):
        return False

    rule_merchant_id = mapping_value(rule, "merchant_id")
    if rule_merchant_id is not None:
        transaction_merchant_id = mapping_value(transaction, "merchant_id")
        return (
            transaction_merchant_id is not None
            and int(transaction_merchant_id) == int(rule_merchant_id)
            and rule_amount_matches(rule, amount)
        )

    keyword = normalize_merchant_description(rule["keyword"])
    normalized_merchant = normalize_merchant(transaction["description"])
    candidates = merchant_match_candidates(
        normalized_merchant.merchant_key,
        normalized_merchant.merchant_key,
        raw_description=transaction["description"],
    )
    return bool(keyword and any(keyword in candidate for candidate in candidates) and rule_amount_matches(rule, amount))


def mapping_value(row: Mapping[str, Any], key: str, default: object | None = None) -> Any:
    """Return a value from a mapping-like SQL row without importing repository helpers."""
    return row[key] if key in row.keys() else row.get(key, default)


def rule_account_matches_transaction(rule: Mapping[str, Any], transaction: Mapping[str, Any]) -> bool:
    """Return whether a transaction satisfies a rule account constraint."""
    rule_account_id = mapping_value(rule, "account_id")
    if rule_account_id is None:
        return True
    transaction_account_id = mapping_value(transaction, "account_id")
    if transaction_account_id is None:
        return False
    return int(rule_account_id) == int(transaction_account_id)


def rule_direction(rule: Mapping[str, Any]) -> str:
    """Return the normalized direction constraint for a rule."""
    direction = str(mapping_value(rule, "direction") or CATEGORY_RULE_DIRECTION_ANY).strip().lower()
    if direction in {CATEGORY_RULE_DIRECTION_ANY, CATEGORY_RULE_DIRECTION_DEBIT, CATEGORY_RULE_DIRECTION_CREDIT}:
        return direction
    return CATEGORY_RULE_DIRECTION_ANY


def rule_direction_matches_transaction(rule: Mapping[str, Any], amount: MoneyValue | None) -> bool:
    """Return whether a transaction amount satisfies a rule direction."""
    direction = rule_direction(rule)
    if direction == CATEGORY_RULE_DIRECTION_ANY:
        return True
    if amount is None:
        return False
    amount = money_to_decimal(amount)
    if direction == CATEGORY_RULE_DIRECTION_DEBIT:
        return amount >= 0
    if direction == CATEGORY_RULE_DIRECTION_CREDIT:
        return amount < 0
    return True


def rule_assignment_metadata(
    rule: Mapping[str, Any],
    category: str,
    tags: Sequence[str],
    confidence: float,
    reason: str,
) -> dict[str, Any]:
    """Return persisted audit metadata for rule-application workflows."""
    rule_id = mapping_value(rule, "id")
    amount_min = mapping_value(rule, "amount_min")
    amount_max = mapping_value(rule, "amount_max")
    return {
        "decision_source": DECISION_SOURCE_RULE,
        "reason": reason,
        "final_category": category,
        "final_tags": list(tags or ()),
        "final_confidence": confidence,
        "review_required": False,
        "matched_rule_id": rule_id,
        "rule_confidence": confidence,
        "rule": {
            "rule_id": rule_id,
            "keyword": rule.get("keyword"),
            "category": category,
            "tags": list(tags or ()),
            "confidence": confidence,
            "amount_min": money_to_float(amount_min) if amount_min is not None else None,
            "amount_max": money_to_float(amount_max) if amount_max is not None else None,
            "account_id": rule.get("account_id"),
            "direction": rule_direction(rule),
            "source": rule.get("source"),
        },
    }


def rule_transaction_kind(category: str, amount: MoneyValue | None, current_kind: str | None = None) -> str:
    """Return transaction kind implied by a rule category and amount direction."""
    if category == TRANSFER_CATEGORY:
        return TRANSACTION_KIND_TRANSFER
    if current_kind == TRANSACTION_KIND_REFUND:
        return TRANSACTION_KIND_REFUND
    return TRANSACTION_KIND_INCOME if money_to_decimal(amount) < 0 else TRANSACTION_KIND_EXPENSE
