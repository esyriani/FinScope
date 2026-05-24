"""LLM-assisted categorization helpers."""

import json
import logging
import re
from threading import local

from sqlalchemy import func, select

from finance_app.core.config import settings
from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_CREDIT,
    CATEGORY_RULE_DIRECTION_DEBIT,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    CATEGORY_RULE_SOURCE_MANUAL,
    UNKNOWN_CATEGORY,
)
from finance_app.core.money import optional_money_to_float
from finance_app.database.tables import (
    tags as tags_table,
    transaction_tags as transaction_tags_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.decision import (
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    apply_review_policy,
    combine_confidence,
    evidence_decision_source,
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
    normalize_tag_names,
)
from finance_app.modules.merchants.normalization import normalize_merchant_description
from finance_app.modules.settings.runtime import get_setting


logger = logging.getLogger(__name__)
_request_context = local()
LLM_BATCH_SIZE = 20
LLM_TIMEOUT_SECONDS = 60
COMMON_CATEGORY_LIMIT = 6
MAX_CANDIDATE_CATEGORIES = 10
MAX_CANDIDATE_TAGS = 16


def clear_llm_request_status():
    """Clear the thread-local status for the next LLM categorization request."""
    _request_context.status = {"status": "not_requested"}


def last_llm_request_status():
    """Return the last thread-local LLM request status for progress logging."""
    return dict(getattr(_request_context, "status", {"status": "not_requested"}))


def record_llm_request_status(status, **fields):
    """Record thread-local LLM request status details."""
    _request_context.status = {"status": status, **fields}


def chunked(items, size):
    """Yield fixed-size chunks from a sequence."""
    for index in range(0, len(items), size):
        yield items[index:index + size]


def pair_llm_results(unknown_items, llm_results):
    """Pair LLM results with the transactions they describe."""
    # New prompts include request_id, but keep positional pairing as a fallback
    # so older or malformed responses can still be interpreted conservatively.
    if llm_results and all(isinstance(result, dict) and "request_id" in result for result in llm_results):
        results_by_id = {
            str(result.get("request_id")): result
            for result in llm_results
            if result.get("request_id") is not None
        }
        for tx in unknown_items:
            result = results_by_id.get(str(tx.get("llm_request_id")))
            if result is not None:
                yield tx, result
        return

    yield from zip(unknown_items, llm_results)


def automatic_rule_amount_bounds(amount):
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


def save_automatic_category_rule(conn, transaction, category, tags):
    """Persist an accepted no-review LLM categorization as an automatic rule."""
    keyword = normalize_merchant_description(
        transaction.get("merchant_key")
        or transaction.get("canonical_merchant")
        or transaction.get("description")
        or ""
    )
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


def automatic_rule_direction(amount):
    """Return the signed direction constraint for an automatic LLM rule."""
    amount = optional_money_to_float(amount)
    if amount is None:
        return CATEGORY_RULE_DIRECTION_ANY
    return CATEGORY_RULE_DIRECTION_CREDIT if amount < 0 else CATEGORY_RULE_DIRECTION_DEBIT


def classify_unknowns_with_llm(conn, transactions, rules, unknown_category):
    """Classify unknowns with LLM."""
    unknown_by_key = {}
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
        return

    category_options = get_category_options(conn)
    tag_options = get_tag_options(conn)
    category_rows = get_category_rows(conn)
    tag_rows = get_tag_rows(conn)
    confidence_threshold = MEDIUM_CONFIDENCE_THRESHOLD
    verify_threshold = HIGH_CONFIDENCE_THRESHOLD
    openai_model = get_setting(conn, "openai_model") or settings.default_categorization_model
    unknown_items = list(unknown_by_key.values())
    for index, tx in enumerate(unknown_items):
        tx["llm_request_id"] = str(index)
    prepare_llm_candidate_taxonomies(
        conn,
        unknown_items,
        category_options,
        tag_options,
        unknown_category,
    )

    accepted = {}
    llm_result_count = 0

    for unknown_chunk in chunked(unknown_items, LLM_BATCH_SIZE):
        chunk_category_options = chunk_candidate_options(
            unknown_chunk,
            "llm_candidate_categories",
            category_options,
        )
        chunk_tag_options = chunk_candidate_options(
            unknown_chunk,
            "llm_candidate_tags",
            tag_options,
        )
        chunk_category_rows = taxonomy_rows_for_names(category_rows, chunk_category_options)
        chunk_tag_rows = taxonomy_rows_for_names(tag_rows, chunk_tag_options)
        llm_results = request_llm_categories(
            unknown_chunk,
            rules,
            chunk_category_options,
            chunk_tag_options,
            chunk_category_rows,
            chunk_tag_rows,
            openai_model,
            verify_threshold,
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

        paired_cache_keys = set()
        for tx, result in pair_llm_results(unknown_chunk, llm_results):
            if not isinstance(result, dict):
                logger.warning("OpenAI categorization returned a non-object result.")
                continue
            merchant_key = tx["merchant_key"]
            cache_key = merchant_category_cache_key(merchant_key, tx.get("amount"), tx.get("merchant_id"))
            paired_cache_keys.add(cache_key)
            allowed_categories = tx.get("llm_candidate_categories") or category_options
            allowed_tags = tx.get("llm_candidate_tags") or tag_options
            allowed_category_rows = taxonomy_rows_for_names(category_rows, allowed_categories)
            allowed_tag_rows = taxonomy_rows_for_names(tag_rows, allowed_tags)
            category, category_id, category_id_is_valid = parse_llm_category_id(
                result.get("category_id"),
                allowed_category_rows,
                unknown_category,
            )
            confidence = parse_confidence(result.get("confidence"))
            confidence_is_valid = 0 <= confidence <= 1
            if not confidence_is_valid:
                confidence = 0.0
            tags, tag_ids, tag_ids_are_valid = parse_llm_tag_ids(
                result.get("tag_ids"),
                allowed_tag_rows,
            )
            final_confidence = (
                llm_final_confidence(tx, category, confidence, result)
                if category_id_is_valid and tag_ids_are_valid and confidence_is_valid
                else 0.0
            )
            decision = apply_review_policy(
                category,
                tags,
                final_confidence,
                unknown_category,
            )

            if decision.category != unknown_category and decision.confidence >= confidence_threshold:
                rule_id = (
                    save_automatic_category_rule(conn, tx, decision.category, decision.tags)
                    if not decision.needs_review
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
                    tag_ids=tag_ids,
                    failure_reason=llm_failure_reason(
                        category,
                        unknown_category,
                        category_id_is_valid,
                        tag_ids_are_valid,
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
    cleanup_llm_candidate_taxonomies(unknown_items)
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


def cleanup_llm_candidate_taxonomies(unknown_items):
    """Remove transient compact taxonomy fields after LLM processing."""
    for tx in unknown_items:
        tx.pop("llm_candidate_categories", None)
        tx.pop("llm_candidate_tags", None)


def llm_final_confidence(transaction, category, confidence, result):
    """Return LLM confidence adjusted by rule and retrieval agreement."""
    agreement_confidences = []
    disagreement_confidences = []
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


def collect_evidence_agreement(evidence, category, agreement_confidences, disagreement_confidences):
    """Append evidence confidence to agreement or disagreement buckets."""
    evidence_category = evidence.get("category")
    evidence_confidence = clamp_llm_evidence_confidence(evidence.get("confidence"))
    if not evidence_category or evidence_confidence is None:
        return
    if evidence_category == category:
        agreement_confidences.append(evidence_confidence)
    elif evidence_confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
        disagreement_confidences.append(evidence_confidence)


def parse_llm_category_id(value, allowed_category_rows, unknown_category):
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


def parse_llm_tag_ids(value, allowed_tag_rows):
    """Return tag names selected by strict LLM tag IDs and whether they are valid."""
    if value in (None, ""):
        return [], [], False
    if not isinstance(value, list):
        return [], [], False

    tags_by_id = {
        str(row.get("id")): row.get("name")
        for row in allowed_tag_rows
        if row.get("id") is not None and row.get("name")
    }
    names = []
    tag_ids = []
    seen = set()
    for item in value:
        key = str(item).strip()
        name = tags_by_id.get(key)
        if not name:
            return [], [], False
        if key in seen:
            continue
        names.append(name)
        tag_ids.append(int(key))
        seen.add(key)
    return names, tag_ids, True


def clamp_llm_evidence_confidence(confidence):
    """Return evidence confidence only when it is a valid probability."""
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return None
    if 0 <= value <= 1:
        return value
    return None


def unknown_llm_result(transaction, unknown_category, failure_reason):
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
    category,
    unknown_category,
    category_id_is_valid,
    tag_ids_are_valid,
    confidence_is_valid,
    decision,
):
    """Return a compact failure reason for conservative LLM outcomes."""
    if not category_id_is_valid:
        return "invalid_category_id"
    if not tag_ids_are_valid:
        return "invalid_tag_ids"
    if not confidence_is_valid:
        return "invalid_confidence"
    if decision.assigned_unknown and decision.proposed_confidence is not None:
        if category == unknown_category:
            return "llm_unknown_category"
        return "confidence_below_medium_threshold"
    if decision.needs_review:
        return "confidence_below_high_threshold"
    return None


def llm_category_metadata(
    transaction,
    result,
    decision,
    llm_confidence,
    final_confidence,
    rule_id,
    category_id=None,
    tag_ids=None,
    failure_reason=None,
):
    """Return persisted audit metadata for an accepted LLM categorization."""
    rule_evidence = transaction.get("rule_evidence")
    historical_evidence = transaction.get("historical_evidence")
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
        "llm_confidence": llm_confidence,
        "llm_reason": str(result.get("reason") or "").strip(),
        "supported_by_similar_transactions": parse_bool(
            result.get("supported_by_similar_transactions")
        ),
    }
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


def prepare_llm_candidate_taxonomies(conn, unknown_items, category_options, tag_options, unknown_category):
    """Attach compact candidate category and tag lists to LLM transaction payloads.

    Candidate taxonomies are intentionally transaction-local. They prioritize
    rule evidence, retrieved historical examples, merchant history, and common
    local categories while preserving a full-taxonomy fallback for weak inputs.
    """
    common_categories = common_category_names(conn, category_options, unknown_category)
    for tx in unknown_items:
        categories = []
        categories.extend(rule_evidence_categories(tx))
        categories.extend(historical_evidence_categories(tx))
        categories.extend(merchant_history_category_names(conn, tx, category_options, unknown_category))
        categories.extend(common_categories)
        categories.append(unknown_category)

        candidate_categories = compact_category_candidates(
            categories,
            category_options,
            unknown_category,
        )
        tx["llm_candidate_categories"] = candidate_categories
        tx["llm_candidate_tags"] = compact_tag_candidates(
            conn,
            tx,
            candidate_categories,
            tag_options,
        )


def rule_evidence_categories(transaction):
    """Return category candidates from matched rule evidence."""
    evidence = transaction.get("rule_evidence") or {}
    return [evidence.get("category")]


def historical_evidence_categories(transaction):
    """Return category candidates from historical retrieval evidence."""
    evidence = transaction.get("historical_evidence") or {}
    categories = [evidence.get("category")]
    categories.extend(example.get("category") for example in evidence.get("examples") or [])
    return categories


def common_category_names(conn, category_options, unknown_category):
    """Return commonly used non-unknown categories from persisted transactions."""
    rows = conn.execute(
        select(
            transactions_table.c.category,
            func.count().label("count"),
        )
        .where(
            transactions_table.c.ignored == 0,
            transactions_table.c.category.is_not(None),
            transactions_table.c.category != unknown_category,
            transactions_table.c.category.in_(category_options),
        )
        .group_by(transactions_table.c.category)
        .order_by(func.count().desc(), transactions_table.c.category)
        .limit(COMMON_CATEGORY_LIMIT)
    ).mappings().fetchall()
    return [row["category"] for row in rows]


def merchant_history_category_names(conn, transaction, category_options, unknown_category):
    """Return categories historically used for the same durable merchant."""
    merchant_id = transaction.get("merchant_id")
    if merchant_id is None:
        return []

    rows = conn.execute(
        select(
            transactions_table.c.category,
            func.count().label("count"),
        )
        .where(
            transactions_table.c.ignored == 0,
            transactions_table.c.merchant_id == int(merchant_id),
            transactions_table.c.category.is_not(None),
            transactions_table.c.category != unknown_category,
            transactions_table.c.category.in_(category_options),
        )
        .group_by(transactions_table.c.category)
        .order_by(func.count().desc(), transactions_table.c.category)
        .limit(COMMON_CATEGORY_LIMIT)
    ).mappings().fetchall()
    return [row["category"] for row in rows]


def compact_category_candidates(categories, category_options, unknown_category):
    """Return compact, valid category candidates with an unknown fallback."""
    candidates = []
    seen = set()
    for category in categories:
        normalized = normalize_candidate_category(category, category_options, unknown_category)
        if not normalized or normalized in seen:
            continue
        candidates.append(normalized)
        seen.add(normalized)

    if unknown_category in category_options and unknown_category not in seen:
        candidates.append(unknown_category)
        seen.add(unknown_category)

    concrete = [category for category in candidates if category != unknown_category]
    if not concrete:
        return list(category_options)

    limited = concrete[: max(1, MAX_CANDIDATE_CATEGORIES - 1)]
    if unknown_category in seen:
        limited.append(unknown_category)
    return limited


def normalize_candidate_category(category, category_options, unknown_category):
    """Normalize a candidate category against the active taxonomy."""
    text = str(category or "").strip()
    if not text:
        return None
    if text == unknown_category or text.upper() == UNKNOWN_CATEGORY:
        return unknown_category if unknown_category in category_options else None
    normalized = normalize_category(text, category_options)
    return normalized if normalized in category_options else None


def compact_tag_candidates(conn, transaction, candidate_categories, tag_options):
    """Return compact, valid tag candidates for one LLM transaction."""
    tags = []
    evidence = transaction.get("rule_evidence") or {}
    tags.extend(evidence.get("tags") or [])
    historical = transaction.get("historical_evidence") or {}
    tags.extend(historical.get("tags") or [])
    for example in historical.get("examples") or []:
        tags.extend(example.get("tags") or [])
    tags.extend(tags_for_candidate_categories(conn, candidate_categories, tag_options))

    normalized = normalize_tag_names(tags, tag_options)
    if normalized:
        return normalized[:MAX_CANDIDATE_TAGS]

    return common_tag_names(conn, tag_options)


def tags_for_candidate_categories(conn, candidate_categories, tag_options):
    """Return tags commonly associated with candidate categories."""
    concrete_categories = [category for category in candidate_categories if category != UNKNOWN_CATEGORY]
    if not concrete_categories or not tag_options:
        return []

    rows = conn.execute(
        select(
            tags_table.c.name,
            func.count().label("count"),
        )
        .select_from(
            transaction_tags_table
            .join(tags_table, tags_table.c.id == transaction_tags_table.c.tag_id)
            .join(transactions_table, transactions_table.c.id == transaction_tags_table.c.transaction_id)
        )
        .where(
            transactions_table.c.category.in_(concrete_categories),
            tags_table.c.name.in_(tag_options),
        )
        .group_by(tags_table.c.name)
        .order_by(func.count().desc(), tags_table.c.name)
        .limit(MAX_CANDIDATE_TAGS)
    ).mappings().fetchall()
    return [row["name"] for row in rows]


def common_tag_names(conn, tag_options):
    """Return commonly used tags as a compact fallback."""
    if not tag_options:
        return []

    rows = conn.execute(
        select(
            tags_table.c.name,
            func.count().label("count"),
        )
        .select_from(transaction_tags_table.join(tags_table, tags_table.c.id == transaction_tags_table.c.tag_id))
        .where(tags_table.c.name.in_(tag_options))
        .group_by(tags_table.c.name)
        .order_by(func.count().desc(), tags_table.c.name)
        .limit(MAX_CANDIDATE_TAGS)
    ).mappings().fetchall()
    names = [row["name"] for row in rows]
    return names or list(tag_options[:MAX_CANDIDATE_TAGS])


def chunk_candidate_options(unknown_chunk, field, all_options):
    """Return the union of per-transaction candidate options for one LLM chunk."""
    selected = set()
    for tx in unknown_chunk:
        selected.update(tx.get(field) or [])
    if not selected:
        return list(all_options)
    return [option for option in all_options if option in selected]


def taxonomy_rows_for_names(rows, names):
    """Return taxonomy rows ordered by a compact name list."""
    rows_by_name = {row["name"]: row for row in rows}
    return [
        rows_by_name.get(name, {"id": None, "name": name, "description": "", "instruction": ""})
        for name in names
    ]


def request_llm_categories(
    unknown_items,
    rules,
    category_options,
    tag_options,
    category_rows,
    tag_rows,
    openai_model,
    verify_threshold,
):
    """Request llm categories."""
    requested_count = len(unknown_items)
    record_llm_request_status("started", requested_count=requested_count)
    if not settings.openai_api_key:
        logger.info("OpenAI API key is not configured; keeping unknown categories unchanged.")
        record_llm_request_status(
            "configuration_missing",
            requested_count=requested_count,
            error_type="Configuration",
            detail="OpenAI API key is not configured.",
        )
        return []

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

    system_prompt = build_llm_system_prompt(category_rows, tag_rows, verify_threshold)
    prompt = build_llm_prompt(
        unknown_items,
        rules,
        category_options,
        tag_options,
        category_rows,
        tag_rows,
    )

    try:
        client = OpenAI(api_key=settings.openai_api_key, timeout=LLM_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=openai_model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": prompt},
            ],
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


def build_llm_system_prompt(category_rows=None, tag_rows=None, verify_threshold=None):
    """Build llm system prompt."""
    if verify_threshold is None:
        verify_threshold = HIGH_CONFIDENCE_THRESHOLD
    category_rows = category_rows or []
    tag_rows = tag_rows or []
    allowed_categories = "\n".join(
        taxonomy_prompt_line(row)
        for row in category_rows
    )
    allowed_tags = "\n".join(
        taxonomy_prompt_line(row)
        for row in tag_rows
    )
    output_categories = ", ".join(str(row["id"]) for row in category_rows if row.get("id") is not None)
    output_tags = ", ".join(str(row["id"]) for row in tag_rows if row.get("id") is not None)

    return f"""You are a financial transaction categorization engine.

Your task is to categorize bank transactions using the transaction description, supplied rule evidence, similar historical transactions, and category/tag instructions below. You must assign exactly one category ID from the allowed category list and zero or more tag IDs from the allowed tag list.

You must be consistent, conservative, and deterministic. Do not invent information. If the description is ambiguous or the category is not clear with high confidence, use "UNKNOWN".

Bank statement context:
- Checking and savings exports often use short bank descriptors, French abbreviations, and terse payment codes. Treat suffixes such as FAC, PAI, ASS, DIV, CC, PLAC, TFR, DEPOT, VIREMENT, CHQ, and VFC as bank context, not as categories by themselves.
- Categorize by the recognizable merchant, biller, institution, or transaction purpose when it is clear despite those codes.
- Common banking examples: utility providers such as Hydro-Quebec, Energir, internet, phone, and similar service providers are Utilities; bank fees, taxes, permits, interest, loans, and government fees are Administrative; transfers, credit card payments, deposits, reimbursements, refunds, and account movements are Transfers.
- Use the provided transaction_kind as a hint only. It describes the ledger direction or non-reporting role already inferred by FinScope, but the merchant and category instructions remain authoritative.

Allowed categories:
{allowed_categories}

Allowed tags:
{allowed_tags or "- No tags are configured."}

Decision rules:
1. Use the transaction description as the primary signal.
2. Use the amount only as a secondary signal. Do not categorize based only on amount.
3. Use the amount direction only when it clearly helps identify Income, refund, fee, rent, or another transaction type.
4. Manual rules may include signed amount_min and amount_max bounds. When present, the rule only applies if the transaction amount is inside that inclusive range. Negative amounts are credits/income/refunds.
5. Normalize merchant names by removing transaction IDs, terminal IDs, dates, authorization codes, city names, card numbers, prefixes such as "POS PURCHASE", and repeated punctuation.
6. Prefer the most specific supported category.
7. If the category is plausible but not clear with high confidence, use "UNKNOWN".
8. Do not create new categories or category IDs.
9. Do not create new tags or tag IDs.
10. Do not infer personal context unless it is directly supported by the description.
11. For refunds, categorize according to the original merchant category when identifiable. Otherwise use "UNKNOWN".
12. Apply tags only when the description or user context clearly supports the tag instruction.
13. Prefer a transaction's candidate_taxonomy when it is provided. It is a compact subset of the allowed taxonomy chosen by FinScope for that transaction.
14. Use similar_transactions as supporting evidence, but do not copy them blindly when they conflict or are weak.

Confidence scoring:
- 0.95 to 1.00: merchant/category is obvious.
- 0.80 to 0.94: likely category with minor ambiguity.
- 0.60 to 0.79: plausible but ambiguous; use "UNKNOWN".
- below 0.60: use "UNKNOWN".

Review rule:
Set "needs_review" to true when:
- category is "UNKNOWN",
- confidence < {verify_threshold:.2f},
- merchant is ambiguous,
- the description is mainly a payment processor without a clear merchant,
- the transaction could plausibly belong to several categories.

Output requirements:
Return only valid JSON.
Do not include markdown.
Do not include explanations outside JSON.
Do not include hidden reasoning or step-by-step reasoning.

For each transaction, output:
{{
  "request_id": string,
  "category_id": one of {output_categories},
  "tag_ids": array containing zero or more of {output_tags or "the configured tag IDs"},
  "confidence": number between 0 and 1,
  "needs_review": boolean,
  "supported_by_similar_transactions": boolean,
  "reason": string
}}"""


def taxonomy_prompt_line(row):
    """Render prompt line."""
    detail = row["instruction"] or row["description"]
    label = f"ID {row.get('id')}: {row['name']}"
    return f"- {label}: {detail}" if detail else f"- {label}"


def build_llm_prompt(unknown_items, rules, category_options, tag_options=None, category_rows=None, tag_rows=None):
    """Build llm prompt."""
    tag_options = tag_options or []
    category_rows = category_rows or []
    tag_rows = tag_rows or []
    examples = build_rule_examples(rules, category_options)
    category_rows_by_name = {row["name"]: row for row in category_rows}
    tag_rows_by_name = {row["name"]: row for row in tag_rows}
    manual_rules = [
        {
            "keyword": normalize_merchant_description(rule["keyword"]),
            "category": normalize_category(rule["category"], category_options),
            "category_id": category_rows_by_name.get(
                normalize_category(rule["category"], category_options),
                {},
            ).get("id"),
            "tags": normalize_tag_names(rule.get("tags"), tag_options),
            "tag_ids": [
                tag_rows_by_name[tag]["id"]
                for tag in normalize_tag_names(rule.get("tags"), tag_options)
                if tag in tag_rows_by_name
            ],
            "amount_min": optional_money_to_float(rule["amount_min"]),
            "amount_max": optional_money_to_float(rule["amount_max"]),
            "account_id": rule.get("account_id"),
            "direction": rule.get("direction") or CATEGORY_RULE_DIRECTION_ANY,
        }
        for rule in rules
        if (
            rule["source"] == CATEGORY_RULE_SOURCE_MANUAL
            and normalize_category(rule["category"], category_options) in category_options
        )
    ]

    payload = {
        "allowed_categories": taxonomy_payload_rows(category_options, category_rows),
        "allowed_tags": taxonomy_payload_rows(tag_options, tag_rows),
        "examples": examples,
        "current_manual_rules": manual_rules,
        "rule_matching": (
            "Manual rules match by normalized keyword containment. "
            "If amount_min or amount_max is present, the signed transaction amount must be inside the inclusive range. "
            "Negative amounts are credits/income/refunds."
        ),
        "transactions": [
            {
                "request_id": tx.get("llm_request_id"),
                "merchant_key": tx["merchant_key"],
                "description": tx.get("description"),
                "amount": optional_money_to_float(tx.get("amount")),
                "date": tx.get("tx_date"),
                "transaction_kind": tx.get("transaction_kind"),
                "best_matching_rule": tx.get("rule_evidence"),
                "similar_transactions": tx.get("historical_evidence"),
                "candidate_taxonomy": {
                    "categories": taxonomy_payload_rows(
                        tx.get("llm_candidate_categories") or category_options,
                        category_rows,
                    ),
                    "tags": taxonomy_payload_rows(
                        tx.get("llm_candidate_tags") or tag_options,
                        tag_rows,
                    ),
                },
                "metadata": {
                    "current_category": tx.get("category"),
                },
            }
            for tx in unknown_items
        ],
        "matching_rule": "Return one result per transaction. Copy request_id exactly from each input transaction.",
        "required_schema": {
            "results": [
                {
                    "request_id": "same request_id as the input transaction",
                    "category_id": "one allowed category id",
                    "tag_ids": ["zero or more allowed tag ids"],
                    "confidence": 0.0,
                    "needs_review": True,
                    "supported_by_similar_transactions": False,
                    "reason": "short explanation",
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def taxonomy_payload_rows(names, taxonomy_rows):
    """Return compact taxonomy metadata for prompt payloads."""
    rows_by_name = {row["name"]: row for row in taxonomy_rows}
    payload = []
    for name in names:
        row = rows_by_name.get(name, {})
        payload.append(
            {
                "id": row.get("id"),
                "name": name,
                "description": row.get("description") or "",
                "instruction": row.get("instruction") or "",
            }
        )
    return payload


def build_rule_examples(rules, category_options):
    """Return the legacy prompt examples payload for supported rule sources.

    Manual rules are sent through `current_manual_rules`, so this compatibility
    payload remains intentionally empty.
    """
    return {}


def parse_confidence(value):
    """Parse confidence."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_bool(value):
    """Parse bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def sanitize_openai_error(exc):
    """Sanitize openai error."""
    message = str(exc)
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", message)
    return message[:500]


def normalize_llm_category(category, allowed_categories, unknown_category):
    """Normalize llm category."""
    text = str(category or "").strip()
    if text.upper() == UNKNOWN_CATEGORY:
        return unknown_category
    normalized = normalize_category(text, allowed_categories)
    if normalized == UNKNOWN_CATEGORY and UNKNOWN_CATEGORY not in allowed_categories:
        return unknown_category
    return normalized
