"""Unit tests for automatic LLM category-rule helper decisions."""

from decimal import Decimal

import pytest

from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_CREDIT,
    CATEGORY_RULE_DIRECTION_DEBIT,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
)
from finance_app.modules.categories import llm_rules


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (None, (None, None)),
        (Decimal("-12.34"), (None, Decimal("0.00"))),
        (Decimal("0.00"), (Decimal("0.00"), None)),
        (Decimal("12.34"), (Decimal("0.00"), None)),
    ],
)
def test_automatic_rule_amount_bounds_preserve_transaction_direction(amount, expected):
    """Verify automatic rule bounds keep future matches on the same signed side."""
    assert llm_rules.automatic_rule_amount_bounds(amount) == expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (None, CATEGORY_RULE_DIRECTION_ANY),
        (Decimal("-0.01"), CATEGORY_RULE_DIRECTION_CREDIT),
        (Decimal("0.00"), CATEGORY_RULE_DIRECTION_DEBIT),
        (Decimal("10.00"), CATEGORY_RULE_DIRECTION_DEBIT),
    ],
)
def test_automatic_rule_direction_uses_signed_amount(amount, expected):
    """Verify automatic rules record an explicit debit or credit direction when possible."""
    assert llm_rules.automatic_rule_direction(amount) == expected


def test_save_automatic_category_rule_skips_transactions_without_keywords(monkeypatch):
    """Verify empty descriptions do not create broad automatic rules."""
    calls = []

    def record_save(*args, **kwargs):
        """Fail the test if persistence is called."""
        calls.append((args, kwargs))
        return 1

    monkeypatch.setattr(llm_rules, "save_category_rule", record_save)

    assert llm_rules.save_automatic_category_rule(object(), {"description": ""}, "Food", []) is None
    assert calls == []


def test_save_automatic_category_rule_passes_normalized_constraints(monkeypatch):
    """Verify persisted automatic rules include direction, amount, owner, and merchant constraints."""
    captured = {}

    def record_save(conn, keyword, category, **kwargs):
        """Capture the rule persistence arguments."""
        captured.update({"conn": conn, "keyword": keyword, "category": category, **kwargs})
        return 123

    conn = object()
    monkeypatch.setattr(llm_rules, "save_category_rule", record_save)

    rule_id = llm_rules.save_automatic_category_rule(
        conn,
        {
            "merchant_key": "Metro Grocery #123",
            "description": "ignored fallback",
            "amount": Decimal("-12.34"),
            "merchant_id": 7,
            "account_id": 9,
        },
        "Food",
        ("Tax",),
    )

    assert rule_id == 123
    assert captured == {
        "conn": conn,
        "keyword": "METRO GROCERY",
        "category": "Food",
        "source": CATEGORY_RULE_SOURCE_AUTOMATIC,
        "amount_min": None,
        "amount_max": Decimal("0.00"),
        "tags": ("Tax",),
        "merchant_id": 7,
        "account_id": 9,
        "direction": CATEGORY_RULE_DIRECTION_CREDIT,
        "protect_user_rule": True,
    }
