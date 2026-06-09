"""Shared categorization decision policy.

Centralizes confidence thresholds, evidence agreement adjustments, and review
state decisions for rule, historical, and LLM categorization paths. Callers
provide source-specific evidence and persist the returned decision fields.
"""

from collections.abc import Iterable
from dataclasses import dataclass

MEDIUM_CONFIDENCE_THRESHOLD = 0.85
HIGH_CONFIDENCE_THRESHOLD = 0.95
AGREEMENT_BONUS = 0.04
SIMILAR_SUPPORT_BONUS = 0.03
DISAGREEMENT_PENALTY = 0.05

DECISION_SOURCE_RULE = "rule"
DECISION_SOURCE_SIMILAR_TRANSACTIONS = "similar_transactions"
DECISION_SOURCE_LLM = "llm"
DECISION_SOURCE_LLM_WITH_SIMILAR_TRANSACTIONS = "llm_with_similar_transactions"
DECISION_SOURCE_COMBINED = "combined"
DECISION_SOURCE_MANUAL = "manual"
DECISION_SOURCE_UNKNOWN = "unknown"
DECISION_SOURCES = frozenset(
    {
        DECISION_SOURCE_RULE,
        DECISION_SOURCE_SIMILAR_TRANSACTIONS,
        DECISION_SOURCE_LLM,
        DECISION_SOURCE_LLM_WITH_SIMILAR_TRANSACTIONS,
        DECISION_SOURCE_COMBINED,
        DECISION_SOURCE_MANUAL,
        DECISION_SOURCE_UNKNOWN,
    }
)


@dataclass(frozen=True)
class FinalCategoryDecision:
    """Represent a category decision after global confidence policy.

    Attributes:
        category: Category to assign after unknown fallback is applied.
        tags: Tags to assign with the category.
        confidence: Persisted confidence, or None when the category is unknown.
        needs_review: Integer review flag used by transaction persistence.
        proposed_category: Category proposed before unknown fallback.
        proposed_confidence: Confidence after evidence combination and before
            unknown fallback clears persisted confidence.
        assigned_unknown: Whether policy replaced the proposal with unknown.
    """

    category: str
    tags: tuple[str, ...]
    confidence: float | None
    needs_review: int
    proposed_category: str | None
    proposed_confidence: float | None
    assigned_unknown: bool


def clamp_confidence(value: object) -> float | None:
    """Return a probability value clamped to the inclusive 0..1 range."""
    try:
        confidence = float(str(value))
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, confidence))


def normalize_decision_source(source: object) -> str:
    """Return a valid JSON audit decision source value.

    `category_source` remains the persisted UI/reporting provenance. This
    helper only normalizes the richer `category_metadata.decision_source`
    audit field written by categorization workflows.
    """
    text = str(source or DECISION_SOURCE_UNKNOWN).strip().lower()
    aliases = {
        "ai": DECISION_SOURCE_LLM,
        "history": DECISION_SOURCE_SIMILAR_TRANSACTIONS,
        "historical": DECISION_SOURCE_SIMILAR_TRANSACTIONS,
    }
    text = aliases.get(text, text)
    return text if text in DECISION_SOURCES else DECISION_SOURCE_UNKNOWN


def evidence_decision_source(
    rule: bool = False,
    retrieval: bool = False,
    llm: bool = False,
    manual: bool = False,
) -> str:
    """Return the controlled decision source for the evidence used.

    The order reflects the final arbiter of the decision while still
    preserving combined evidence when multiple non-manual signals materially
    influenced the category.
    """
    if manual:
        return DECISION_SOURCE_MANUAL
    if llm:
        if retrieval:
            return DECISION_SOURCE_LLM_WITH_SIMILAR_TRANSACTIONS
        if rule:
            return DECISION_SOURCE_COMBINED
        return DECISION_SOURCE_LLM
    if rule and retrieval:
        return DECISION_SOURCE_COMBINED
    if retrieval:
        return DECISION_SOURCE_SIMILAR_TRANSACTIONS
    if rule:
        return DECISION_SOURCE_RULE
    return DECISION_SOURCE_UNKNOWN


def combine_confidence(
    base_confidence: object,
    agreement_confidences: Iterable[object] = (),
    disagreement_confidences: Iterable[object] = (),
    supported_by_similar: bool = False,
) -> float | None:
    """Return final confidence after agreement and disagreement adjustments.

    Agreement with another evidence source raises confidence toward the
    strongest agreeing signal. Disagreement lowers confidence and keeps it
    below the high-confidence threshold while preserving useful medium
    decisions for manual review.
    """
    confidence = clamp_confidence(base_confidence)
    if confidence is None:
        return None

    agreement_values = [
        value for value in (clamp_confidence(item) for item in agreement_confidences) if value is not None
    ]
    disagreement_values = [
        value for value in (clamp_confidence(item) for item in disagreement_confidences) if value is not None
    ]

    if agreement_values:
        confidence = max(confidence, *agreement_values) + AGREEMENT_BONUS
    if supported_by_similar:
        confidence += SIMILAR_SUPPORT_BONUS
    if disagreement_values:
        confidence = min(confidence - DISAGREEMENT_PENALTY, HIGH_CONFIDENCE_THRESHOLD - DISAGREEMENT_PENALTY)
        if confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
            confidence = max(MEDIUM_CONFIDENCE_THRESHOLD, confidence)

    return round(max(0.0, min(0.99, confidence)), 4)


def apply_review_policy(
    category: str | None,
    tags: Iterable[str] | None,
    confidence: object,
    unknown_category: str,
) -> FinalCategoryDecision:
    """Return the final assignment and review state for a proposed category.

    The shared policy assigns unknown when confidence is below the medium
    threshold, requires review for medium-confidence known categories, and
    clears review only for known categories at or above the high threshold.
    """
    proposed_confidence = clamp_confidence(confidence)
    proposed_category = category
    if (
        not category
        or category == unknown_category
        or proposed_confidence is None
        or proposed_confidence < MEDIUM_CONFIDENCE_THRESHOLD
    ):
        return FinalCategoryDecision(
            category=unknown_category,
            tags=(),
            confidence=None,
            needs_review=1,
            proposed_category=proposed_category,
            proposed_confidence=proposed_confidence,
            assigned_unknown=True,
        )

    rounded_confidence = round(proposed_confidence, 4)
    return FinalCategoryDecision(
        category=category,
        tags=tuple(tags or ()),
        confidence=rounded_confidence,
        needs_review=1 if rounded_confidence < HIGH_CONFIDENCE_THRESHOLD else 0,
        proposed_category=proposed_category,
        proposed_confidence=rounded_confidence,
        assigned_unknown=False,
    )
