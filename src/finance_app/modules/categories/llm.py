"""LLM-assisted categorization helpers."""

import json
import logging
import re
from collections.abc import Iterable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from threading import local
from typing import Any

from finance_app.core.config import settings
from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_CREDIT,
    CATEGORY_RULE_DIRECTION_DEBIT,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    UNKNOWN_CATEGORY,
)
from finance_app.core.money import MoneyValue, optional_money_to_float
from finance_app.modules.categories.decision import (
    MEDIUM_CONFIDENCE_THRESHOLD,
    FinalCategoryDecision,
    apply_review_policy,
    combine_confidence,
    evidence_decision_source,
)
from finance_app.modules.categories.llm_prompts import (
    build_llm_messages,
)
from finance_app.modules.categories.llm_prompts import (
    build_llm_prompt as build_llm_prompt,
)
from finance_app.modules.categories.llm_prompts import (
    build_llm_system_prompt as build_llm_system_prompt,
)
from finance_app.modules.categories.llm_taxonomy import (
    prepare_llm_candidate_taxonomies,
    taxonomy_ids_for_names,
)
from finance_app.modules.categories.repository import (
    get_category_options,
    normalize_category,
    resolve_category_id,
    save_category_rule,
)
from finance_app.modules.categories.rules_matching import merchant_category_cache_key
from finance_app.modules.categories.sources import CATEGORY_SOURCE_AI, category_assignment
from finance_app.modules.categories.taxonomy import (
    get_category_rows,
    get_tag_options,
    get_tag_rows,
)
from finance_app.modules.merchants.normalization import normalize_merchant_description
from finance_app.modules.settings.runtime import get_float_setting, get_setting

logger = logging.getLogger(__name__)
_request_context = local()
LLM_BATCH_SIZE = 20
LLM_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class LlmCategorizationRequestContext:
    """Prepared context for an LLM categorization request or estimate."""

    unknown_items: list[MutableMapping[str, Any]]
    category_options: Sequence[str]
    tag_options: Sequence[str]
    category_rows: Sequence[Mapping[str, Any]]
    tag_rows: Sequence[Mapping[str, Any]]
    confidence_threshold: float
    review_threshold: float
    verify_threshold: float
    openai_model: str


def clear_llm_request_status() -> None:
    """Clear the thread-local status for the next LLM categorization request."""
    _request_context.status = {"status": "not_requested"}


def last_llm_request_status() -> dict[str, Any]:
    """Return the last thread-local LLM request status for progress logging."""
    return dict(getattr(_request_context, "status", {"status": "not_requested"}))


def record_llm_request_status(status: str, **fields: Any) -> None:
    """Record thread-local LLM request status details."""
    _request_context.status = {"status": status, **fields}


def chunked(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    """Yield fixed-size chunks from a sequence."""
    for index in range(0, len(items), size):
        yield items[index : index + size]


def pair_llm_results(
    unknown_items: Sequence[MutableMapping[str, Any]],
    llm_results: Sequence[Any],
) -> Iterator[tuple[MutableMapping[str, Any], Any]]:
    """Pair LLM results with the transactions they describe."""
    # New prompts include request_id, but keep positional pairing as a fallback
    # so older or malformed responses can still be interpreted conservatively.
    if llm_results and all(isinstance(result, dict) and "request_id" in result for result in llm_results):
        results_by_id = {
            str(result.get("request_id")): result for result in llm_results if result.get("request_id") is not None
        }
        for tx in unknown_items:
            result = results_by_id.get(str(tx.get("llm_request_id")))
            if result is not None:
                yield tx, result
        return

    yield from zip(unknown_items, llm_results)


def automatic_rule_amount_bounds(amount: MoneyValue | None) -> tuple[float | None, float | None]:
    """Return signed amount bounds for an automatically created rule.

    Automatic categorization deduplicates candidate transactions by merchant and
    amount direction. Persisting the same direction boundary on the generated
    rule keeps future rule matches aligned with that decision scope.
    """
    if amount is None:
        return None, None

    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None, None

    if value < 0:
        return None, 0
    return 0, None


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
    amount = optional_money_to_float(amount)
    if amount is None:
        return CATEGORY_RULE_DIRECTION_ANY
    return CATEGORY_RULE_DIRECTION_CREDIT if amount < 0 else CATEGORY_RULE_DIRECTION_DEBIT


def prepare_llm_categorization_request_context(
    conn: Any,
    transactions: Sequence[MutableMapping[str, Any]],
    unknown_category: str,
    prepare_candidate_taxonomies: Any = None,
) -> LlmCategorizationRequestContext | None:
    """Prepare deduplicated unknown items and prompt context for the LLM."""
    prepare_candidate_taxonomies = prepare_candidate_taxonomies or prepare_llm_candidate_taxonomies
    unknown_by_key: dict[Any, MutableMapping[str, Any]] = {}
    # Deduplicate by merchant and amount before calling the model; equivalent
    # unknown transactions should receive the same accepted classification.
    for tx in transactions:
        if tx.get("category") == unknown_category and tx.get("merchant_key"):
            cache_key = merchant_category_cache_key(
                tx["merchant_key"],
                tx.get("amount"),
                tx.get("merchant_id"),
            )
            unknown_by_key.setdefault(cache_key, tx)

    if not unknown_by_key:
        return None

    category_options = get_category_options(conn)
    tag_options = get_tag_options(conn)
    category_rows = get_category_rows(conn)
    tag_rows = get_tag_rows(conn)
    confidence_threshold = get_float_setting(
        conn,
        "llm_confidence_threshold",
        settings.default_llm_confidence_threshold,
        minimum=0,
        maximum=1,
    )
    review_threshold = get_float_setting(
        conn,
        "llm_review_threshold",
        settings.default_llm_review_threshold,
        minimum=0,
        maximum=1,
    )
    verify_threshold = get_float_setting(
        conn,
        "verify_threshold",
        settings.default_verify_threshold,
        minimum=0,
        maximum=1,
    )
    openai_model = get_setting(conn, "openai_model") or settings.default_categorization_model
    unknown_items = list(unknown_by_key.values())
    for index, tx in enumerate(unknown_items):
        tx["llm_request_id"] = str(index)
    prepare_candidate_taxonomies(
        conn,
        unknown_items,
        category_options,
        tag_options,
        unknown_category,
        category_rows,
        tag_rows,
    )
    return LlmCategorizationRequestContext(
        unknown_items=unknown_items,
        category_options=category_options,
        tag_options=tag_options,
        category_rows=category_rows,
        tag_rows=tag_rows,
        confidence_threshold=confidence_threshold,
        review_threshold=review_threshold,
        verify_threshold=verify_threshold,
        openai_model=openai_model,
    )


def classify_unknowns_with_llm(
    conn: Any,
    transactions: Sequence[MutableMapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
    unknown_category: str,
    save_automatic_rules: bool = True,
    request_categories: Any = None,
    prepare_candidate_taxonomies: Any = None,
    batch_size: int | None = None,
) -> None:
    """Classify unknowns with LLM.

    When ``save_automatic_rules`` is false, accepted high-confidence results
    are applied to the provided transaction payloads without creating reusable
    automatic rules. This supports one-off suggestion previews from transaction
    detail screens. ``request_categories`` can inject the model request
    function for tests or alternate callers without replacing module globals.
    ``prepare_candidate_taxonomies`` and ``batch_size`` provide the same
    explicit injection points for candidate-taxonomy setup and batching.
    """
    request_categories = request_categories or request_llm_categories
    batch_size = batch_size or LLM_BATCH_SIZE
    context = prepare_llm_categorization_request_context(
        conn,
        transactions,
        unknown_category,
        prepare_candidate_taxonomies=prepare_candidate_taxonomies,
    )
    if context is None:
        return

    accepted: dict[Any, dict[str, Any]] = {}
    llm_result_count = 0

    for unknown_chunk in chunked(context.unknown_items, batch_size):
        llm_results = request_categories(
            unknown_chunk,
            rules,
            context.category_options,
            context.tag_options,
            context.category_rows,
            context.tag_rows,
            context.openai_model,
            context.verify_threshold,
            context.review_threshold,
        )
        llm_result_count += len(llm_results)

        if not llm_results:
            logger.info("LLM categorization returned no results for one chunk; continuing with later chunks.")
            for tx in unknown_chunk:
                cache_key = merchant_category_cache_key(
                    tx["merchant_key"],
                    tx.get("amount"),
                    tx.get("merchant_id"),
                )
                accepted[cache_key] = unknown_llm_result(
                    tx,
                    unknown_category,
                    failure_reason="llm_no_results",
                )
            continue

        if llm_results and len(llm_results) != len(unknown_chunk):
            logger.warning(
                "OpenAI categorization returned %s results for %s requested items.",
                len(llm_results),
                len(unknown_chunk),
            )

        paired_cache_keys: set[Any] = set()
        for tx, result in pair_llm_results(unknown_chunk, llm_results):
            if not isinstance(result, dict):
                logger.warning("OpenAI categorization returned a non-object result.")
                continue
            merchant_key = tx["merchant_key"]
            cache_key = merchant_category_cache_key(merchant_key, tx.get("amount"), tx.get("merchant_id"))
            paired_cache_keys.add(cache_key)
            candidate_categories = tx.get("llm_candidate_categories") or context.category_options
            candidate_tags = tx.get("llm_candidate_tags") or context.tag_options
            candidate_category_ids = taxonomy_ids_for_names(context.category_rows, candidate_categories)
            candidate_tag_ids = taxonomy_ids_for_names(context.tag_rows, candidate_tags)
            category, category_id, category_id_is_valid = parse_llm_category_id(
                result.get("category_id"),
                context.category_rows,
                unknown_category,
            )
            confidence = parse_confidence(result.get("confidence"))
            confidence_is_valid = 0 <= confidence <= 1
            if not confidence_is_valid:
                confidence = 0.0
            tags, tag_ids, invalid_tag_ids, tag_ids_payload_is_valid = parse_llm_tag_ids(
                result.get("tag_ids"),
                context.tag_rows,
            )
            category_outside_candidate_taxonomy = (
                category_id_is_valid and category_id is not None and category_id not in set(candidate_category_ids)
            )
            tag_ids_outside_candidate_taxonomy = [tag_id for tag_id in tag_ids if tag_id not in set(candidate_tag_ids)]
            tag_drop = filtered_llm_tags_for_validity(
                tags,
                tag_ids,
            )
            final_confidence = (
                llm_final_confidence(tx, category, confidence, result)
                if category_id_is_valid and confidence_is_valid
                else 0.0
            )
            decision = apply_llm_review_policy(
                category,
                tag_drop["tags"],
                final_confidence,
                unknown_category,
                context.review_threshold,
                context.verify_threshold,
            )
            if llm_result_needs_forced_review(
                decision,
                category_outside_candidate_taxonomy,
                tag_ids_outside_candidate_taxonomy,
                invalid_tag_ids,
                tag_ids_payload_is_valid,
                tag_drop["dropped_outside_candidate_tag_ids"],
            ):
                decision = FinalCategoryDecision(
                    category=decision.category,
                    tags=decision.tags,
                    confidence=decision.confidence,
                    needs_review=1,
                    proposed_category=decision.proposed_category,
                    proposed_confidence=decision.proposed_confidence,
                    assigned_unknown=decision.assigned_unknown,
                )

            if (
                decision.category != unknown_category
                and decision.confidence is not None
                and decision.confidence >= context.confidence_threshold
            ):
                rule_id = (
                    save_automatic_category_rule(conn, tx, decision.category, decision.tags)
                    if save_automatic_rules and not decision.needs_review
                    else None
                )
            else:
                rule_id = None
            accepted[cache_key] = {
                "category": decision.category,
                "confidence": decision.confidence,
                "needs_review": bool(decision.needs_review),
                "tags": list(decision.tags),
                "rule_id": rule_id,
                "metadata": llm_category_metadata(
                    tx,
                    result,
                    decision,
                    confidence,
                    final_confidence,
                    rule_id,
                    category_id=category_id,
                    tag_ids=tag_drop["tag_ids"],
                    candidate_category_ids=candidate_category_ids,
                    candidate_tag_ids=candidate_tag_ids,
                    category_outside_candidate_taxonomy=category_outside_candidate_taxonomy,
                    tag_ids_outside_candidate_taxonomy=tag_ids_outside_candidate_taxonomy,
                    dropped_invalid_tag_ids=invalid_tag_ids,
                    dropped_tag_ids_outside_candidate_taxonomy=tag_drop["dropped_outside_candidate_tag_ids"],
                    tag_ids_payload_is_valid=tag_ids_payload_is_valid,
                    failure_reason=llm_failure_reason(
                        category,
                        unknown_category,
                        category_id_is_valid,
                        confidence_is_valid,
                        decision,
                    ),
                ),
            }
        for tx in unknown_chunk:
            cache_key = merchant_category_cache_key(
                tx["merchant_key"],
                tx.get("amount"),
                tx.get("merchant_id"),
            )
            if cache_key not in paired_cache_keys:
                accepted[cache_key] = unknown_llm_result(
                    tx,
                    unknown_category,
                    failure_reason="llm_missing_result",
                )
    logger.info("LLM categorization returned %s result(s).", llm_result_count)
    cleanup_llm_candidate_taxonomies(context.unknown_items)
    if not accepted:
        return

    # Apply accepted model results back to the original transaction list, not
    # only the deduplicated request list.
    for tx in transactions:
        cache_key = merchant_category_cache_key(
            tx.get("merchant_key"),
            tx.get("amount"),
            tx.get("merchant_id"),
        )
        if tx.get("category") == unknown_category and cache_key in accepted:
            accepted_result = accepted[cache_key]
            tx["category"] = accepted_result["category"]
            tx["category_id"] = resolve_category_id(conn, accepted_result["category"])
            tx["tags"] = accepted_result["tags"]
            tx["needs_review"] = 1 if accepted_result["needs_review"] else 0
            tx.update(
                category_assignment(
                    accepted_result["category"],
                    unknown_category,
                    CATEGORY_SOURCE_AI,
                    confidence=accepted_result["confidence"],
                    rule_id=accepted_result["rule_id"],
                    metadata=accepted_result["metadata"],
                ).to_dict()
            )


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


def request_llm_categories(
    unknown_items: Sequence[Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
    category_options: Sequence[str],
    tag_options: Sequence[str],
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
    openai_model: str,
    verify_threshold: float,
    review_threshold: float,
    client_factory: Any = None,
    api_key: str | None = None,
) -> list[Any]:
    """Request LLM categories.

    Args:
        unknown_items: Transaction payloads to categorize.
        rules: Matching manual and automatic rule context.
        category_options: Category names available for validation.
        tag_options: Tag names available for validation.
        category_rows: Structured category taxonomy rows.
        tag_rows: Structured tag taxonomy rows.
        openai_model: Model name to request.
        verify_threshold: Confidence threshold for verified results.
        review_threshold: Confidence floor for review suggestions.
        client_factory: Optional OpenAI-compatible client constructor for tests.
        api_key: Optional API key override for tests or alternate callers.

    Returns:
        Parsed result dictionaries, or an empty list when the provider cannot
        return usable JSON.
    """
    requested_count = len(unknown_items)
    record_llm_request_status("started", requested_count=requested_count)
    effective_api_key = settings.openai_api_key if api_key is None else api_key
    if not effective_api_key:
        logger.info("OpenAI API key is not configured; keeping unknown categories unchanged.")
        record_llm_request_status(
            "configuration_missing",
            requested_count=requested_count,
            error_type="Configuration",
            detail="OpenAI API key is not configured.",
        )
        return []

    if client_factory is None:
        try:
            from openai import OpenAI
        except ImportError:
            logger.warning("OpenAI package is not installed; keeping unknown categories unchanged.")
            record_llm_request_status(
                "dependency_missing",
                requested_count=requested_count,
                error_type="ImportError",
                detail="OpenAI package is not installed.",
            )
            return []
        client_factory = OpenAI

    messages = build_llm_messages(
        unknown_items,
        rules,
        category_options,
        tag_options,
        category_rows,
        tag_rows,
        verify_threshold,
        review_threshold,
    )

    try:
        client = client_factory(api_key=effective_api_key, timeout=LLM_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=openai_model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=messages,
        )
        content = response.choices[0].message.content
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("OpenAI categorization response was not valid JSON: %s", exc)
        record_llm_request_status(
            "invalid_json",
            requested_count=requested_count,
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return []
    except Exception as exc:
        detail = sanitize_openai_error(exc)
        logger.warning(
            "OpenAI categorization request failed: %s: %s",
            type(exc).__name__,
            detail,
        )
        record_llm_request_status(
            "request_error",
            requested_count=requested_count,
            error_type=type(exc).__name__,
            detail=detail,
        )
        return []

    results = payload.get("results")
    if not isinstance(results, list):
        logger.warning("OpenAI categorization response did not include a results list.")
        record_llm_request_status(
            "invalid_response",
            requested_count=requested_count,
            error_type="Invalid response",
            detail="The response did not include a results list.",
        )
        return []
    record_llm_request_status(
        "ok",
        requested_count=requested_count,
        result_count=len(results),
    )
    return results if isinstance(results, list) else []


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
