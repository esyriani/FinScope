"""Unit tests for historical categorization scoring helpers."""

from decimal import Decimal

import pytest

from finance_app.core.constants import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_MANUAL,
    CATEGORY_SOURCE_RULE,
)
from finance_app.modules.categories.history import (
    HistoricalCandidate,
    amount_similarity,
    candidate_authority,
    date_similarity,
    historical_decision_from_candidates,
    same_direction,
    same_optional_value,
    supported_tags,
    text_similarity,
)


def history_candidate(
    transaction_id=1,
    category="Food",
    score=0.90,
    authority=1.0,
    tags=(),
):
    """Build a historical candidate for pure scoring tests."""
    return HistoricalCandidate(
        transaction_id=transaction_id,
        tx_date="2026-01-01",
        description="Metro Grocery",
        amount=12.34,
        transaction_kind="expense",
        source=CATEGORY_SOURCE_MANUAL,
        category=category,
        tags=tuple(tags),
        score=score,
        authority=authority,
    )


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("", "Metro", 0.0),
        ("Metro", "", 0.0),
        ("Metro", "Metro", 1.0),
        ("Metro", "Metro Grocery", 5 / 13),
    ],
)
def test_text_similarity_handles_empty_exact_and_containment_cases(left, right, expected):
    """Verify short merchant similarity branches before fuzzy matching is needed."""
    assert text_similarity(left, right) == expected
    assert 0 < text_similarity("Metro", "Metra") < 1


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (Decimal("10.00"), Decimal("10.00"), 1.0),
        (Decimal("10.00"), Decimal("5.00"), 0.5),
        (Decimal("10.00"), Decimal("-10.00"), 0.0),
        (None, Decimal("10.00"), 0.0),
    ],
)
def test_amount_similarity_requires_same_direction_and_scales_difference(left, right, expected):
    """Verify amount similarity compares absolute values only within the same signed direction."""
    assert amount_similarity(left, right) == expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (None, Decimal("1.00"), False),
        (Decimal("1.00"), Decimal("-1.00"), False),
        (Decimal("-1.00"), Decimal("-2.00"), True),
        (Decimal("0.00"), Decimal("2.00"), True),
    ],
)
def test_same_direction_requires_two_amounts_with_matching_signs(left, right, expected):
    """Verify direction matching distinguishes spending and credit values."""
    assert same_direction(left, right) is expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (None, 1, False),
        (1, None, False),
        ("7", 7, True),
        ("7", 8, False),
    ],
)
def test_same_optional_value_requires_present_equal_integer_values(left, right, expected):
    """Verify optional integer-like identifiers only match when both values are present and equal."""
    assert same_optional_value(left, right) is expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("bad", "2026-01-01", 0.0),
        ("2026-01-01", "bad", 0.0),
        ("2026-01-01", "2026-01-01", 1.0),
        ("2026-01-01", "2025-01-01", 1 / (1 + 365 / 365)),
    ],
)
def test_date_similarity_handles_invalid_exact_and_distance_cases(left, right, expected):
    """Verify date similarity returns a bounded recency signal."""
    assert date_similarity(left, right) == expected


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"category_source": CATEGORY_SOURCE_MANUAL, "reviewed_at": None, "category_confidence": None}, 1.15),
        ({"category_source": "unknown", "reviewed_at": "2026-01-01T00:00:00Z", "category_confidence": None}, 1.05),
        ({"category_source": CATEGORY_SOURCE_RULE, "reviewed_at": None, "category_confidence": None}, 0.95),
        ({"category_source": CATEGORY_SOURCE_AI, "reviewed_at": None, "category_confidence": Decimal("0.50")}, 0.65),
        ({"category_source": CATEGORY_SOURCE_AI, "reviewed_at": None, "category_confidence": Decimal("0.99")}, 0.90),
        ({"category_source": CATEGORY_SOURCE_AI, "reviewed_at": None, "category_confidence": None}, 0.70),
        ({"category_source": "unknown", "reviewed_at": None, "category_confidence": None}, 0.70),
    ],
)
def test_candidate_authority_weights_reviewed_manual_rule_and_ai_sources(row, expected):
    """Verify historical evidence authority reflects source reliability."""
    assert candidate_authority(row) == expected


def test_historical_decision_from_candidates_handles_empty_evidence():
    """Verify no historical candidates produce a no-decision result."""
    decision = historical_decision_from_candidates([])

    assert decision.category is None
    assert decision.tags == ()
    assert decision.confidence == 0.0
    assert decision.evidence_ids == ()
    assert decision.candidates == ()


def test_historical_decision_from_candidates_supports_exceptional_single_match():
    """Verify one very strong manual candidate can produce a high-confidence decision."""
    decision = historical_decision_from_candidates(
        [history_candidate(transaction_id=10, score=0.96, authority=1.15, tags=("Tax",))]
    )

    assert decision.category == "Food"
    assert decision.confidence >= 0.95
    assert decision.evidence_ids == (10,)
    assert decision.tags == ("Tax",)
    assert decision.is_high_confidence is True


def test_historical_decision_from_candidates_distinguishes_medium_and_low_confidence():
    """Verify vote share and candidate strength drive confidence bands."""
    medium = historical_decision_from_candidates(
        [
            history_candidate(transaction_id=1, category="Food", score=0.80, authority=1.0),
            history_candidate(transaction_id=2, category="Personal", score=0.20, authority=1.0),
        ]
    )
    low = historical_decision_from_candidates(
        [
            history_candidate(transaction_id=3, category="Food", score=0.60, authority=1.0),
            history_candidate(transaction_id=4, category="Personal", score=0.40, authority=1.0),
        ]
    )

    assert medium.category == "Food"
    assert medium.is_medium_confidence is True
    assert medium.is_high_confidence is False
    assert low.category == "Food"
    assert low.confidence < 0.85


def test_supported_tags_require_weight_or_strong_single_candidate_support():
    """Verify tags need enough support from the winning category candidates."""
    strong_single = [history_candidate(score=0.90, authority=1.0, tags=("Tax", "Shared"))]
    split_support = [
        history_candidate(score=0.70, authority=1.0, tags=("Tax",)),
        history_candidate(transaction_id=2, score=0.60, authority=1.0, tags=("Tax", "Shared")),
    ]

    assert supported_tags(strong_single, winning_weight=0.90) == ["Shared", "Tax"]
    assert supported_tags(split_support, winning_weight=1.30) == ["Tax"]
    assert supported_tags([], winning_weight=1.0) == []
    assert supported_tags(split_support, winning_weight=0.0) == []
