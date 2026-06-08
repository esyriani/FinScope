"""Tests for shared categorization decision policy."""

from finance_app.modules.categories.decision import (
    DECISION_SOURCE_COMBINED,
    DECISION_SOURCE_LLM,
    DECISION_SOURCE_LLM_WITH_SIMILAR_TRANSACTIONS,
    DECISION_SOURCE_MANUAL,
    DECISION_SOURCE_RULE,
    DECISION_SOURCE_SIMILAR_TRANSACTIONS,
    DECISION_SOURCE_UNKNOWN,
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    apply_review_policy,
    combine_confidence,
    evidence_decision_source,
    normalize_decision_source,
)


def test_apply_review_policy_enforces_global_threshold_boundaries():
    """Verify unknown fallback, review, and no-review thresholds."""
    low = apply_review_policy("Food", ["Tax"], MEDIUM_CONFIDENCE_THRESHOLD - 0.01, "UNKNOWN")
    medium = apply_review_policy("Food", ["Tax"], MEDIUM_CONFIDENCE_THRESHOLD, "UNKNOWN")
    high = apply_review_policy("Food", ["Tax"], HIGH_CONFIDENCE_THRESHOLD, "UNKNOWN")

    assert low.category == "UNKNOWN"
    assert low.confidence is None
    assert low.needs_review == 1
    assert low.tags == ()

    assert medium.category == "Food"
    assert medium.confidence == MEDIUM_CONFIDENCE_THRESHOLD
    assert medium.needs_review == 1
    assert medium.tags == ("Tax",)

    assert high.category == "Food"
    assert high.confidence == HIGH_CONFIDENCE_THRESHOLD
    assert high.needs_review == 0


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

    assert agreement >= HIGH_CONFIDENCE_THRESHOLD
    assert MEDIUM_CONFIDENCE_THRESHOLD <= disagreement < HIGH_CONFIDENCE_THRESHOLD


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


def test_normalize_decision_source_maps_legacy_values():
    """Verify old metadata values normalize to current audit source names."""
    assert normalize_decision_source("ai") == DECISION_SOURCE_LLM
    assert normalize_decision_source("history") == DECISION_SOURCE_SIMILAR_TRANSACTIONS
    assert normalize_decision_source("historical") == DECISION_SOURCE_SIMILAR_TRANSACTIONS
    assert normalize_decision_source("manual") == DECISION_SOURCE_MANUAL
    assert normalize_decision_source("not-a-source") == DECISION_SOURCE_UNKNOWN
