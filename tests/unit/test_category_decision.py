"""Tests for shared categorization decision policy."""

import pytest

from finance_app.core.config import settings
from finance_app.modules.categories.decision import (
    DECISION_SOURCE_COMBINED,
    DECISION_SOURCE_LLM,
    DECISION_SOURCE_LLM_WITH_SIMILAR_TRANSACTIONS,
    DECISION_SOURCE_MANUAL,
    DECISION_SOURCE_RULE,
    DECISION_SOURCE_SIMILAR_TRANSACTIONS,
    DECISION_SOURCE_UNKNOWN,
    apply_review_policy,
    clamp_confidence,
    combine_confidence,
    evidence_decision_source,
    normalize_decision_source,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("bad", None), (-0.1, 0.0), (0, 0.0), ("0.42", 0.42), (1, 1.0), (1.1, 1.0)],
)
def test_clamp_confidence_preserves_probability_bounds(value, expected):
    """Invalid inputs stay unknown and numeric evidence stays within probability bounds."""
    assert clamp_confidence(value) == expected


def test_apply_review_policy_enforces_global_threshold_boundaries():
    """Verify unknown fallback, review, and no-review thresholds."""
    low = apply_review_policy("Food", ["Tax"], 0.69, "UNKNOWN", review_threshold=0.70, verify_threshold=0.80)
    medium = apply_review_policy("Food", ["Tax"], 0.70, "UNKNOWN", review_threshold=0.70, verify_threshold=0.80)
    high = apply_review_policy("Food", ["Tax"], 0.80, "UNKNOWN", review_threshold=0.70, verify_threshold=0.80)

    assert low.category == "UNKNOWN"
    assert low.confidence is None
    assert low.needs_review == 1
    assert low.tags == ()
    assert low.assigned_unknown is True

    assert medium.category == "Food"
    assert medium.confidence == 0.70
    assert medium.needs_review == 1
    assert medium.tags == ("Tax",)
    assert medium.assigned_unknown is False

    assert high.category == "Food"
    assert high.confidence == 0.80
    assert high.needs_review == 0
    assert high.assigned_unknown is False


def test_combine_confidence_rewards_agreement_and_penalizes_disagreement():
    """Verify evidence agreement increases confidence and disagreement keeps review required."""
    agreement = combine_confidence(
        0.89,
        agreement_confidences=(0.90,),
        supported_by_similar=True,
    )
    disagreement = combine_confidence(
        0.96,
        agreement_confidences=(0.88,),
        disagreement_confidences=(0.95,),
    )

    assert agreement >= settings.default_verify_threshold
    assert settings.default_category_review_threshold <= disagreement < settings.default_verify_threshold


def test_evidence_decision_source_uses_controlled_values():
    """Verify evidence combinations map to the audit decision-source vocabulary."""
    assert evidence_decision_source(manual=True, rule=True, retrieval=True, llm=True) == DECISION_SOURCE_MANUAL
    assert evidence_decision_source(rule=True) == DECISION_SOURCE_RULE
    assert evidence_decision_source(retrieval=True) == DECISION_SOURCE_SIMILAR_TRANSACTIONS
    assert evidence_decision_source(rule=True, retrieval=True) == DECISION_SOURCE_COMBINED
    assert evidence_decision_source(llm=True) == DECISION_SOURCE_LLM
    assert evidence_decision_source(rule=True, llm=True) == DECISION_SOURCE_COMBINED
    assert (
        evidence_decision_source(rule=True, retrieval=True, llm=True) == DECISION_SOURCE_LLM_WITH_SIMILAR_TRANSACTIONS
    )
    assert evidence_decision_source() == DECISION_SOURCE_UNKNOWN


def test_normalize_decision_source_maps_source_aliases():
    """Verify metadata source aliases normalize to current audit source names."""
    assert normalize_decision_source("ai") == DECISION_SOURCE_LLM
    assert normalize_decision_source("history") == DECISION_SOURCE_SIMILAR_TRANSACTIONS
    assert normalize_decision_source("historical") == DECISION_SOURCE_SIMILAR_TRANSACTIONS
    assert normalize_decision_source("manual") == DECISION_SOURCE_MANUAL
    assert normalize_decision_source("not-a-source") == DECISION_SOURCE_UNKNOWN
