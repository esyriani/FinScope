"""LLM categorization result parsing and review policy helpers.

Provider requests and batch orchestration live in ``categories.llm``. This
module owns validation, confidence adjustment, conservative fallback metadata,
and other pure result-shaping logic for model responses.
"""

import re
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from typing import Any

from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.modules.categories.decision import (
    MEDIUM_CONFIDENCE_THRESHOLD,
    FinalCategoryDecision,
    apply_review_policy,
    combine_confidence,
    evidence_decision_source,
)
from finance_app.modules.categories.repository import normalize_category


def cleanup_llm_candidate_taxonomies(unknown_items: Sequence[MutableMapping[str, Any]]) -> None:
    """Remove transient compact taxonomy fields after LLM processing."""
    for tx in unknown_items:
        tx.pop("llm_candidate_categories", None)
        tx.pop("llm_candidate_tags", None)


def filtered_llm_tags_for_validity(tags: Sequence[str], tag_ids: Sequence[int]) -> dict[str, Any]:
    """Return valid taxonomy tags selected by the LLM.

    Candidate tags are prompt hints, not an acceptance gate. Invalid IDs are
    removed before this helper receives values from `parse_llm_tag_ids`.
    """
    return {
        "tags": list(tags),
        "tag_ids": list(tag_ids),
        "dropped_outside_candidate_tag_ids": [],
    }


def llm_result_needs_forced_review(
    decision: FinalCategoryDecision,
    category_outside_candidate_taxonomy: bool,
    tag_ids_outside_candidate_taxonomy: Sequence[int],
    invalid_tag_ids: Sequence[Any],
    tag_ids_payload_is_valid: bool,
    dropped_tag_ids_outside_candidate_taxonomy: Sequence[int],
) -> bool:
    """Return whether malformed LLM taxonomy output should force review."""
    del category_outside_candidate_taxonomy, tag_ids_outside_candidate_taxonomy
    if decision.assigned_unknown:
        return False
    return any(
        (
            invalid_tag_ids,
            not tag_ids_payload_is_valid,
            dropped_tag_ids_outside_candidate_taxonomy,
        )
    )


def llm_final_confidence(
    transaction: Mapping[str, Any],
    category: str,
    confidence: object,
    result: Mapping[str, Any],
) -> float | None:
    """Return LLM confidence adjusted by rule and retrieval agreement."""
    agreement_confidences: list[float] = []
    disagreement_confidences: list[float] = []
    rule_evidence = transaction.get("rule_evidence") or {}
    historical_evidence = transaction.get("historical_evidence") or {}

    collect_evidence_agreement(rule_evidence, category, agreement_confidences, disagreement_confidences)
    collect_evidence_agreement(historical_evidence, category, agreement_confidences, disagreement_confidences)

    return combine_confidence(
        confidence,
        agreement_confidences=agreement_confidences,
        disagreement_confidences=disagreement_confidences,
        supported_by_similar=(
            parse_bool(result.get("supported_by_similar_transactions"))
            and historical_evidence.get("category") == category
        ),
    )


def collect_evidence_agreement(
    evidence: Mapping[str, Any],
    category: str,
    agreement_confidences: list[float],
    disagreement_confidences: list[float],
) -> None:
    """Append evidence confidence to agreement or disagreement buckets."""
    evidence_category = evidence.get("category")
    evidence_confidence = clamp_llm_evidence_confidence(evidence.get("confidence"))
    if not evidence_category or evidence_confidence is None:
        return
    if evidence_category == category:
        agreement_confidences.append(evidence_confidence)
    elif evidence_confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
        disagreement_confidences.append(evidence_confidence)


def apply_llm_review_policy(
    category: str,
    tags: Sequence[str],
    confidence: object,
    unknown_category: str,
    review_threshold: float,
    verify_threshold: float,
) -> FinalCategoryDecision:
    """Return the final assignment and review state for an LLM proposal.

    LLM output is allowed to keep lower-confidence best-fit suggestions for
    manual review. Shared rule and historical matching stay stricter through
    `apply_review_policy`, while the LLM path preserves useful taxonomy
    suggestions instead of flattening them to UNKNOWN.
    """
    proposed_confidence = clamp_llm_evidence_confidence(confidence)
    proposed_category = category
    if (
        not category
        or category == unknown_category
        or proposed_confidence is None
        or proposed_confidence < review_threshold
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
        needs_review=1 if rounded_confidence < verify_threshold else 0,
        proposed_category=proposed_category,
        proposed_confidence=rounded_confidence,
        assigned_unknown=False,
    )


def parse_llm_category_id(
    value: object,
    allowed_category_rows: Sequence[Mapping[str, Any]],
    unknown_category: str,
) -> tuple[str, int | None, bool]:
    """Return the category selected by a strict LLM category ID."""
    category_by_id = {
        str(row.get("id")): row.get("name")
        for row in allowed_category_rows
        if row.get("id") is not None and row.get("name")
    }
    category = category_by_id.get(str(value).strip())
    if category:
        if category == UNKNOWN_CATEGORY:
            category = unknown_category
        return category, int(str(value).strip()), True
    return unknown_category, None, False


def parse_llm_tag_ids(
    value: object,
    allowed_tag_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[int], list[Any], bool]:
    """Return valid tag selections and invalid values from an LLM tag payload."""
    if value in (None, ""):
        return [], [], [], False
    if not isinstance(value, list):
        return [], [], [value], False

    tags_by_id = {
        str(row.get("id")): row.get("name") for row in allowed_tag_rows if row.get("id") is not None and row.get("name")
    }
    names: list[str] = []
    tag_ids: list[int] = []
    invalid_tag_ids: list[Any] = []
    seen: set[str] = set()
    for item in value:
        key = str(item).strip()
        name = tags_by_id.get(key)
        if not name:
            invalid_tag_ids.append(item)
            continue
        if key in seen:
            continue
        names.append(name)
        tag_ids.append(int(key))
        seen.add(key)
    return names, tag_ids, invalid_tag_ids, True


def clamp_llm_evidence_confidence(confidence: object) -> float | None:
    """Return evidence confidence only when it is a valid probability."""
    try:
        value = float(str(confidence))
    except (TypeError, ValueError):
        return None
    if 0 <= value <= 1:
        return value
    return None


def unknown_llm_result(
    transaction: Mapping[str, Any],
    unknown_category: str,
    failure_reason: str,
) -> dict[str, Any]:
    """Return an explicit unknown result for an LLM failure path."""
    decision = apply_review_policy(unknown_category, (), 0.0, unknown_category)
    return {
        "category": decision.category,
        "confidence": decision.confidence,
        "needs_review": bool(decision.needs_review),
        "tags": [],
        "rule_id": None,
        "metadata": llm_category_metadata(
            transaction,
            {},
            decision,
            llm_confidence=0.0,
            final_confidence=0.0,
            rule_id=None,
            category_id=None,
            tag_ids=[],
            failure_reason=failure_reason,
        ),
    }


def llm_failure_reason(
    category: str,
    unknown_category: str,
    category_id_is_valid: bool,
    confidence_is_valid: bool,
    decision: FinalCategoryDecision,
) -> str | None:
    """Return a compact failure reason for conservative LLM outcomes."""
    if not category_id_is_valid:
        return "invalid_category_id"
    if not confidence_is_valid:
        return "invalid_confidence"
    if decision.assigned_unknown and decision.proposed_confidence is not None:
        if category == unknown_category:
            return "llm_unknown_category"
        return "confidence_below_review_threshold"
    return None


def llm_category_metadata(
    transaction: Mapping[str, Any],
    result: Mapping[str, Any],
    decision: FinalCategoryDecision,
    llm_confidence: float,
    final_confidence: float | None,
    rule_id: int | None,
    category_id: int | None = None,
    tag_ids: Sequence[int] | None = None,
    candidate_category_ids: Sequence[int] | None = None,
    candidate_tag_ids: Sequence[int] | None = None,
    category_outside_candidate_taxonomy: bool = False,
    tag_ids_outside_candidate_taxonomy: Sequence[int] | None = None,
    dropped_invalid_tag_ids: Sequence[Any] | None = None,
    dropped_tag_ids_outside_candidate_taxonomy: Sequence[int] | None = None,
    tag_ids_payload_is_valid: bool = True,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    """Return persisted audit metadata for an accepted LLM categorization."""
    rule_evidence = transaction.get("rule_evidence")
    historical_evidence = transaction.get("historical_evidence")
    tag_ids_outside_candidate_taxonomy = list(tag_ids_outside_candidate_taxonomy or [])
    dropped_invalid_tag_ids = list(dropped_invalid_tag_ids or [])
    dropped_tag_ids_outside_candidate_taxonomy = list(dropped_tag_ids_outside_candidate_taxonomy or [])
    metadata = {
        "decision_source": evidence_decision_source(
            rule=bool(rule_evidence),
            retrieval=bool(historical_evidence),
            llm=True,
        ),
        "final_category": decision.category,
        "final_tags": list(decision.tags),
        "final_confidence": decision.confidence,
        "proposed_category": decision.proposed_category,
        "proposed_confidence": final_confidence,
        "review_required": bool(decision.needs_review),
        "category_rule_id": rule_id,
        "llm_category_id": category_id,
        "llm_tag_ids": list(tag_ids or []),
        "candidate_category_ids": list(candidate_category_ids or []),
        "candidate_tag_ids": list(candidate_tag_ids or []),
        "category_outside_candidate_taxonomy": bool(category_outside_candidate_taxonomy),
        "full_taxonomy_fallback_used": bool(category_outside_candidate_taxonomy and not decision.assigned_unknown),
        "full_taxonomy_fallback_rejected": False,
        "llm_confidence": llm_confidence,
        "llm_reason": str(result.get("reason") or "").strip(),
        "supported_by_similar_transactions": parse_bool(result.get("supported_by_similar_transactions")),
    }
    if tag_ids_outside_candidate_taxonomy:
        metadata["tag_ids_outside_candidate_taxonomy"] = tag_ids_outside_candidate_taxonomy
    if dropped_invalid_tag_ids:
        metadata["dropped_invalid_tag_ids"] = dropped_invalid_tag_ids
    if dropped_tag_ids_outside_candidate_taxonomy:
        metadata["dropped_tag_ids_outside_candidate_taxonomy"] = dropped_tag_ids_outside_candidate_taxonomy
    if not tag_ids_payload_is_valid:
        metadata["tag_ids_payload_is_valid"] = False
    if failure_reason:
        metadata["failure_reason"] = failure_reason
    if rule_evidence:
        metadata["rule"] = rule_evidence
        metadata["matched_rule_id"] = rule_evidence.get("rule_id")
        metadata["rule_confidence"] = rule_evidence.get("confidence")
        metadata["rule_agreed_with_llm"] = rule_evidence.get("category") == decision.proposed_category
    if historical_evidence:
        metadata["retrieval"] = historical_evidence
        metadata["retrieval_confidence"] = historical_evidence.get("confidence")
        metadata["similar_transaction_ids"] = list(historical_evidence.get("evidence_ids") or [])
        metadata["retrieval_agreed_with_llm"] = historical_evidence.get("category") == decision.proposed_category
    return metadata


def parse_confidence(value: object) -> float:
    """Parse confidence."""
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def parse_bool(value: object) -> bool:
    """Parse bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def sanitize_openai_error(exc: BaseException) -> str:
    """Sanitize openai error."""
    message = str(exc)
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", message)
    return message[:500]


def normalize_llm_category(category: object, allowed_categories: Iterable[str], unknown_category: str) -> str:
    """Normalize llm category."""
    text = str(category or "").strip()
    if text.upper() == UNKNOWN_CATEGORY:
        return unknown_category
    normalized = normalize_category(text, allowed_categories)
    if normalized == UNKNOWN_CATEGORY and UNKNOWN_CATEGORY not in allowed_categories:
        return unknown_category
    return normalized
