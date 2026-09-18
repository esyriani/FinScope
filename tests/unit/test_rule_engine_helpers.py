"""Unit tests for pure category-rule engine helpers."""

from decimal import Decimal

import pytest

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
from finance_app.modules.rules.engine import (
    mapping_value,
    rule_account_matches_transaction,
    rule_assignment_metadata,
    rule_direction,
    rule_direction_matches_transaction,
    rule_matches_transaction,
    rule_preview_matches_transaction,
    rule_transaction_kind,
)


def category_rule(**overrides):
    """Build a rule mapping for pure engine tests."""
    values = {
        "id": 42,
        "merchant_id": None,
        "account_id": None,
        "keyword": "METRO",
        "category": "Food",
        "amount_min": None,
        "amount_max": None,
        "direction": CATEGORY_RULE_DIRECTION_ANY,
        "source": "manual",
    }
    values.update(overrides)
    return values


def transaction(**overrides):
    """Build a transaction mapping for pure engine tests."""
    values = {
        "id": 7,
        "description": "Metro Grocery #123",
        "amount": Decimal("12.34"),
        "account_id": 10,
        "merchant_id": 20,
        "transaction_kind": TRANSACTION_KIND_EXPENSE,
    }
    values.update(overrides)
    return values


def test_mapping_value_handles_present_and_missing_keys():
    """Verify mapping reads use row-style key checks and explicit defaults."""
    assert mapping_value({"present": 1}, "present") == 1
    assert mapping_value({}, "missing") is None
    assert mapping_value({}, "missing", "fallback") == "fallback"


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (None, CATEGORY_RULE_DIRECTION_ANY),
        ("", CATEGORY_RULE_DIRECTION_ANY),
        (" CREDIT ", CATEGORY_RULE_DIRECTION_CREDIT),
        ("debit", CATEGORY_RULE_DIRECTION_DEBIT),
        ("nonsense", CATEGORY_RULE_DIRECTION_ANY),
    ],
)
def test_rule_direction_normalizes_supported_values(direction, expected):
    """Verify direction constraints are normalized to the supported vocabulary."""
    assert rule_direction(category_rule(direction=direction)) == expected
    assert rule_direction({"keyword": "METRO"}) == CATEGORY_RULE_DIRECTION_ANY


@pytest.mark.parametrize(
    ("rule_overrides", "amount", "expected"),
    [
        ({}, None, True),
        ({"direction": CATEGORY_RULE_DIRECTION_DEBIT}, None, False),
        ({"direction": CATEGORY_RULE_DIRECTION_DEBIT}, Decimal("0.00"), True),
        ({"direction": CATEGORY_RULE_DIRECTION_DEBIT}, Decimal("12.34"), True),
        ({"direction": CATEGORY_RULE_DIRECTION_DEBIT}, Decimal("-0.50"), False),
        ({"direction": CATEGORY_RULE_DIRECTION_DEBIT}, Decimal("-12.34"), False),
        ({"direction": CATEGORY_RULE_DIRECTION_CREDIT}, Decimal("-0.50"), True),
        ({"direction": CATEGORY_RULE_DIRECTION_CREDIT}, Decimal("-12.34"), True),
        ({"direction": CATEGORY_RULE_DIRECTION_CREDIT}, Decimal("0.00"), False),
        ({"direction": "unsupported"}, Decimal("-12.34"), True),
    ],
)
def test_rule_direction_matches_transaction_uses_signed_amounts(rule_overrides, amount, expected):
    """Verify rule direction checks distinguish debits, credits, and unconstrained rules."""
    assert rule_direction_matches_transaction(category_rule(**rule_overrides), amount) is expected


@pytest.mark.parametrize(
    ("rule_account_id", "transaction_account_id", "expected"),
    [
        (None, None, True),
        (None, 10, True),
        (10, None, False),
        (10, "10", True),
        ("1000", "1000", True),
        ("10", 11, False),
    ],
)
def test_rule_account_matches_transaction_handles_optional_constraints(
    rule_account_id,
    transaction_account_id,
    expected,
):
    """Verify optional account constraints do not match missing or different accounts."""
    assert (
        rule_account_matches_transaction(
            category_rule(account_id=rule_account_id),
            transaction(account_id=transaction_account_id),
        )
        is expected
    )


@pytest.mark.parametrize(
    ("matcher", "rule_merchant_id", "transaction_merchant_id", "expected"),
    [
        ("preview", "1000", "999", False),
        ("preview", "1000", "1000", True),
        ("preview", "1000", "1001", False),
        ("apply", "1000", "999", False),
        ("apply", "1000", "1000", True),
    ],
)
def test_merchant_bound_matching_uses_value_equality_for_large_ids(
    matcher,
    rule_merchant_id,
    transaction_merchant_id,
    expected,
):
    """Verify merchant-bound matching is strict value equality, not ordering or identity."""
    rule = category_rule(merchant_id=rule_merchant_id)
    tx = transaction(merchant_id=transaction_merchant_id)

    if matcher == "preview":
        actual = rule_preview_matches_transaction(rule, tx, "IGNORED")
    else:
        actual = rule_matches_transaction(rule, tx)

    assert actual is expected


@pytest.mark.parametrize(
    ("rule_overrides", "transaction_overrides", "expected"),
    [
        ({}, {}, True),
        ({"account_id": 99}, {}, False),
        ({"direction": CATEGORY_RULE_DIRECTION_CREDIT}, {}, False),
        ({"merchant_id": 20}, {}, True),
        ({"merchant_id": 20}, {"merchant_id": None}, False),
        ({"merchant_id": 20}, {"merchant_id": 21}, False),
        ({"keyword": ""}, {}, False),
        ({"keyword": "IGA"}, {}, False),
        ({"amount_min": Decimal("20.00")}, {}, False),
        ({"amount_max": Decimal("10.00")}, {}, False),
        ({"category": "Income", "keyword": "PAYROLL"}, {"description": "Payroll Deposit"}, False),
        (
            {"category": "Income", "keyword": "PAYROLL"},
            {"description": "Payroll Deposit", "amount": Decimal("-1000.00")},
            True,
        ),
    ],
)
def test_rule_matches_transaction_covers_keyword_merchant_and_amount_branches(
    rule_overrides,
    transaction_overrides,
    expected,
):
    """Verify apply matching covers merchant, keyword, amount, account, direction, and income guards."""
    assert rule_matches_transaction(category_rule(**rule_overrides), transaction(**transaction_overrides)) is expected


@pytest.mark.parametrize(
    ("matcher", "amount", "expected"),
    [
        ("preview", Decimal("-0.50"), True),
        ("preview", Decimal("0.00"), False),
        ("preview", Decimal("0.50"), False),
        ("apply", Decimal("-0.50"), True),
        ("apply", Decimal("0.00"), False),
        ("apply", Decimal("0.50"), False),
    ],
)
def test_income_category_matching_uses_zero_as_the_signed_boundary(matcher, amount, expected):
    """Verify income rules only match strictly negative signed amounts."""
    rule = category_rule(category="Income", keyword="PAYROLL")
    tx = transaction(description="Payroll Deposit", amount=amount)

    if matcher == "preview":
        actual = rule_preview_matches_transaction(rule, tx, "PAYROLL")
    else:
        actual = rule_matches_transaction(rule, tx)

    assert actual is expected


@pytest.mark.parametrize(
    ("rule_overrides", "transaction_overrides", "keyword", "expected"),
    [
        ({}, {}, "METRO", True),
        ({}, {}, "IGA", False),
        ({"account_id": 99}, {}, "METRO", False),
        ({"direction": CATEGORY_RULE_DIRECTION_CREDIT}, {}, "METRO", False),
        ({"merchant_id": 20}, {}, "IGNORED", True),
        ({"merchant_id": 20}, {"merchant_id": None}, "METRO", False),
        ({"category": "Income"}, {}, "METRO", False),
        ({"amount_min": Decimal("20.00")}, {}, "METRO", False),
    ],
)
def test_rule_preview_matches_transaction_covers_preview_specific_branches(
    rule_overrides,
    transaction_overrides,
    keyword,
    expected,
):
    """Verify preview matching covers keyword and merchant-bound branches."""
    assert (
        rule_preview_matches_transaction(
            category_rule(**rule_overrides),
            transaction(**transaction_overrides),
            keyword,
        )
        is expected
    )


def test_rule_assignment_metadata_serializes_rule_audit_fields():
    """Verify rule audit metadata stores normalized rule details."""
    metadata = rule_assignment_metadata(
        category_rule(
            amount_min=Decimal("1.25"),
            amount_max=Decimal("20.00"),
            account_id=10,
            direction="unexpected",
            source="automatic",
        ),
        "Food",
        ("Tax", "Shared"),
        0.91,
        "Matched by rule",
    )

    assert metadata["decision_source"] == "rule"
    assert metadata["reason"] == "Matched by rule"
    assert metadata["final_tags"] == ["Tax", "Shared"]
    assert metadata["review_required"] is False
    assert metadata["rule"] == {
        "rule_id": 42,
        "keyword": "METRO",
        "category": "Food",
        "tags": ["Tax", "Shared"],
        "confidence": 0.91,
        "amount_min": 1.25,
        "amount_max": 20.0,
        "account_id": 10,
        "direction": CATEGORY_RULE_DIRECTION_ANY,
        "source": "automatic",
    }


@pytest.mark.parametrize(
    ("category", "amount", "current_kind", "expected"),
    [
        (TRANSFER_CATEGORY, Decimal("12.34"), None, TRANSACTION_KIND_TRANSFER),
        ("".join(("Transfer", "s")), Decimal("12.34"), None, TRANSACTION_KIND_TRANSFER),
        ("Food", Decimal("12.34"), TRANSACTION_KIND_REFUND, TRANSACTION_KIND_REFUND),
        ("Food", Decimal("12.34"), "".join(("re", "fund")), TRANSACTION_KIND_REFUND),
        ("Food", Decimal("-12.34"), None, TRANSACTION_KIND_INCOME),
        ("Food", Decimal("-0.50"), None, TRANSACTION_KIND_INCOME),
        ("Zoo", Decimal("12.34"), None, TRANSACTION_KIND_EXPENSE),
        ("Food", Decimal("0.00"), None, TRANSACTION_KIND_EXPENSE),
        ("Food", None, None, TRANSACTION_KIND_EXPENSE),
    ],
)
def test_rule_transaction_kind_preserves_transfer_refund_and_signed_cash_flow(
    category,
    amount,
    current_kind,
    expected,
):
    """Verify rule application preserves special kinds before falling back to amount direction."""
    assert rule_transaction_kind(category, amount, current_kind=current_kind) == expected
