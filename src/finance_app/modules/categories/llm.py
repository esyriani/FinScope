"""LLM-assisted categorization helpers."""

import json
import logging
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from threading import local
from typing import Any

from finance_app.core.config import settings
from finance_app.database.engine import CORE_DB_TRANSACTION_DEPTH_KEY
from finance_app.modules.categories.decision import (
    FinalCategoryDecision,
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
from finance_app.modules.categories.llm_results import (
    apply_llm_review_policy,
    cleanup_llm_candidate_taxonomies,
    filtered_llm_tags_for_validity,
    llm_category_metadata,
    llm_failure_reason,
    llm_final_confidence,
    llm_result_needs_forced_review,
    parse_confidence,
    parse_llm_category_id,
    parse_llm_tag_ids,
    sanitize_openai_error,
    unknown_llm_result,
)
from finance_app.modules.categories.llm_rules import save_automatic_category_rule
from finance_app.modules.categories.llm_taxonomy import (
    prepare_llm_candidate_taxonomies,
    taxonomy_ids_for_names,
)
from finance_app.modules.categories.repository import (
    get_category_options,
    resolve_category_id,
)
from finance_app.modules.categories.rules_matching import merchant_category_cache_key
from finance_app.modules.categories.sources import CATEGORY_SOURCE_AI, category_assignment
from finance_app.modules.categories.taxonomy import (
    get_category_rows,
    get_tag_options,
    get_tag_rows,
)
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


@dataclass(frozen=True)
class LlmCategorizationOutcome:
    """Validated LLM results ready to apply in a database write phase."""

    accepted: dict[Any, dict[str, Any]]
    result_count: int = 0


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
    # Prompts include request_id; positional pairing keeps malformed responses
    # conservative when the identifier is missing.
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
    context = prepare_llm_categorization_request_context(
        conn,
        transactions,
        unknown_category,
        prepare_candidate_taxonomies=prepare_candidate_taxonomies,
    )
    if context is None:
        return

    require_released_transaction_for_default_provider(conn, request_categories)
    outcome = request_llm_categorization_outcome(
        context,
        rules,
        unknown_category,
        request_categories=request_categories,
        batch_size=batch_size,
    )
    apply_llm_categorization_outcome(
        conn,
        transactions,
        outcome,
        unknown_category,
        save_automatic_rules=save_automatic_rules,
    )


def require_released_transaction_for_default_provider(conn: Any, request_categories: Any = None) -> None:
    """Reject default provider calls while a database transaction is active."""
    if request_categories is not None:
        return

    in_transaction = bool(getattr(conn, "in_transaction", lambda: False)())
    logical_depth = int(getattr(conn, "info", {}).get(CORE_DB_TRANSACTION_DEPTH_KEY, 0) or 0)
    if in_transaction or logical_depth:
        raise RuntimeError(
            "Default LLM categorization requests must use the split prepare/request/apply workflow "
            "so provider calls run outside database transactions."
        )


def request_llm_categorization_outcome(
    context: LlmCategorizationRequestContext,
    rules: Sequence[Mapping[str, Any]],
    unknown_category: str,
    request_categories: Any = None,
    batch_size: int | None = None,
) -> LlmCategorizationOutcome:
    """Request and validate LLM categorization results without database writes."""
    request_categories = request_categories or request_llm_categories
    batch_size = batch_size or LLM_BATCH_SIZE
    accepted: dict[Any, dict[str, Any]] = {}
    llm_result_count = 0

    try:
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
                cache_key = merchant_category_cache_key(
                    tx["merchant_key"],
                    tx.get("amount"),
                    tx.get("merchant_id"),
                )
                paired_cache_keys.add(cache_key)
                accepted[cache_key] = validated_llm_result(
                    tx,
                    result,
                    context,
                    unknown_category,
                )
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
    finally:
        cleanup_llm_candidate_taxonomies(context.unknown_items)

    logger.info("LLM categorization returned %s result(s).", llm_result_count)
    return LlmCategorizationOutcome(accepted=accepted, result_count=llm_result_count)


def validated_llm_result(
    tx: MutableMapping[str, Any],
    result: Mapping[str, Any],
    context: LlmCategorizationRequestContext,
    unknown_category: str,
) -> dict[str, Any]:
    """Return a validated, database-write-free LLM result for one transaction."""
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
        llm_final_confidence(tx, category, confidence, result) if category_id_is_valid and confidence_is_valid else 0.0
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

    automatic_rule_candidate = (
        decision.category != unknown_category
        and decision.confidence is not None
        and decision.confidence >= context.confidence_threshold
        and not decision.needs_review
    )
    return {
        "category": decision.category,
        "confidence": decision.confidence,
        "needs_review": bool(decision.needs_review),
        "tags": list(decision.tags),
        "rule_id": None,
        "automatic_rule_candidate": automatic_rule_candidate,
        "automatic_rule_checked": False,
        "metadata": llm_category_metadata(
            tx,
            result,
            decision,
            confidence,
            final_confidence,
            None,
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


def apply_llm_categorization_outcome(
    conn: Any,
    transactions: Sequence[MutableMapping[str, Any]],
    outcome: LlmCategorizationOutcome,
    unknown_category: str,
    save_automatic_rules: bool = True,
) -> None:
    """Apply validated LLM results inside the caller's database write phase."""
    accepted = outcome.accepted
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
            rule_id = automatic_rule_id_for_result(
                conn,
                tx,
                accepted_result,
                save_automatic_rules=save_automatic_rules,
            )
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
                    rule_id=rule_id,
                    metadata=accepted_result["metadata"],
                ).to_dict()
            )


def automatic_rule_id_for_result(
    conn: Any,
    transaction: Mapping[str, Any],
    accepted_result: MutableMapping[str, Any],
    save_automatic_rules: bool,
) -> int | None:
    """Return or create the automatic rule ID for an accepted LLM result."""
    if accepted_result.get("automatic_rule_checked"):
        return accepted_result.get("rule_id")

    rule_id = None
    if save_automatic_rules and accepted_result.get("automatic_rule_candidate"):
        rule_id = save_automatic_category_rule(
            conn,
            transaction,
            accepted_result["category"],
            accepted_result["tags"],
        )
    accepted_result["rule_id"] = rule_id
    accepted_result["automatic_rule_checked"] = True
    metadata = accepted_result.get("metadata")
    if isinstance(metadata, dict):
        metadata["category_rule_id"] = rule_id
    return rule_id


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
