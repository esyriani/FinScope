"""Token-estimate orchestration for assembled LLM categorization requests.

This module prepares the same final chat payloads used by the categorization
provider and aggregates per-batch token estimates. It does not call external
LLM providers or mutate persisted categorization state.
"""

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from finance_app.core.config import settings
from finance_app.modules.categories import llm as llm_module
from finance_app.modules.categories.llm_prompts import build_llm_messages
from finance_app.modules.categories.llm_tokens import DEFAULT_EXPECTED_OUTPUT_TOKENS, estimate_llm_chat_tokens
from finance_app.modules.settings.runtime import get_setting

LLM_EXPECTED_OUTPUT_TOKENS_PER_RESULT = 80
MODEL_CONTEXT_LIMIT_TOKENS = {
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1-nano": 1_000_000,
}
MODEL_CONTEXT_LIMIT_PREFIXES = (
    ("gpt-4o-mini-", 128_000),
    ("gpt-4.1-mini-", 1_000_000),
    ("gpt-4.1-nano-", 1_000_000),
    ("gpt-4.1-", 1_000_000),
)


def expected_llm_output_tokens(requested_count: int) -> int:
    """Return an output-token planning allowance for one categorization batch."""
    return max(DEFAULT_EXPECTED_OUTPUT_TOKENS, requested_count * LLM_EXPECTED_OUTPUT_TOKENS_PER_RESULT)


def model_context_limit_tokens(model: str) -> int | None:
    """Return a known per-request context limit for an OpenAI model id."""
    normalized = str(model or "").strip().lower()
    if not normalized:
        return None
    limit = MODEL_CONTEXT_LIMIT_TOKENS.get(normalized)
    if limit is not None:
        return limit
    for prefix, prefix_limit in MODEL_CONTEXT_LIMIT_PREFIXES:
        if normalized.startswith(prefix):
            return prefix_limit
    return None


def context_limit_fields(model: str, usage_tokens: int) -> dict[str, Any]:
    """Return context-limit metadata for one model request when known."""
    limit = model_context_limit_tokens(model)
    if limit is None:
        return {
            "context_limit_tokens": None,
            "context_usage_tokens": None,
            "context_usage_ratio": None,
        }
    bounded_usage = max(0, int(usage_tokens or 0))
    return {
        "context_limit_tokens": limit,
        "context_usage_tokens": bounded_usage,
        "context_usage_ratio": bounded_usage / limit if limit else None,
    }


def estimate_llm_categorization_tokens(
    conn: Any,
    transactions: Sequence[MutableMapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
    unknown_category: str,
    prepare_candidate_taxonomies: Any = None,
    batch_size: int | None = None,
    encoding_factory: Any = None,
) -> dict[str, Any]:
    """Estimate tokens for the final LLM categorization request batches."""
    batch_size = batch_size or llm_module.LLM_BATCH_SIZE
    context = llm_module.prepare_llm_categorization_request_context(
        conn,
        transactions,
        unknown_category,
        prepare_candidate_taxonomies=prepare_candidate_taxonomies,
    )
    if context is None:
        model = get_setting(conn, "openai_model") or settings.default_categorization_model
        return empty_llm_token_estimate(model)

    batch_estimates: list[dict[str, Any]] = []
    for index, unknown_chunk in enumerate(llm_module.chunked(context.unknown_items, batch_size), start=1):
        messages = build_llm_messages(
            unknown_chunk,
            rules,
            context.category_options,
            context.tag_options,
            context.category_rows,
            context.tag_rows,
            context.verify_threshold,
            context.review_threshold,
        )
        output_tokens = expected_llm_output_tokens(len(unknown_chunk))
        estimate = estimate_llm_chat_tokens(
            messages,
            context.openai_model,
            output_tokens,
            encoding_factory=encoding_factory,
        )
        batch_estimates.append(
            {
                "batch_index": index,
                "request_count": len(unknown_chunk),
                "input_tokens": estimate.input_tokens,
                "expected_output_tokens": estimate.expected_output_tokens,
                "total_tokens": estimate.total_tokens,
                "tokenizer": estimate.tokenizer,
                "tokenizer_available": estimate.tokenizer_available,
                "warning": estimate.warning,
            }
        )

    llm_module.cleanup_llm_candidate_taxonomies(context.unknown_items)
    return summarize_llm_token_estimates(context.openai_model, len(context.unknown_items), batch_estimates)


def empty_llm_token_estimate(model: str) -> dict[str, Any]:
    """Return a zero-token summary when no LLM request would be sent."""
    return {
        "model": model,
        "request_count": 0,
        "batch_count": 0,
        "input_tokens": 0,
        "expected_output_tokens": 0,
        "total_tokens": 0,
        "max_batch_input_tokens": 0,
        "max_batch_total_tokens": 0,
        "tokenizer": "",
        "tokenizer_available": True,
        "warning": None,
        "batches": [],
        **context_limit_fields(model, 0),
    }


def summarize_llm_token_estimates(
    model: str,
    request_count: int,
    batches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return aggregate token estimate fields for several request batches."""
    warnings = [str(batch.get("warning")) for batch in batches if batch.get("warning")]
    tokenizer_available = all(bool(batch.get("tokenizer_available")) for batch in batches)
    max_batch_total_tokens = max((int(batch.get("total_tokens") or 0) for batch in batches), default=0)
    return {
        "model": model,
        "request_count": request_count,
        "batch_count": len(batches),
        "input_tokens": sum(int(batch.get("input_tokens") or 0) for batch in batches),
        "expected_output_tokens": sum(int(batch.get("expected_output_tokens") or 0) for batch in batches),
        "total_tokens": sum(int(batch.get("total_tokens") or 0) for batch in batches),
        "max_batch_input_tokens": max((int(batch.get("input_tokens") or 0) for batch in batches), default=0),
        "max_batch_total_tokens": max_batch_total_tokens,
        "tokenizer": str(batches[0].get("tokenizer") or "") if batches else "",
        "tokenizer_available": tokenizer_available,
        "warning": "; ".join(dict.fromkeys(warnings)) or None,
        "batches": [dict(batch) for batch in batches],
        **context_limit_fields(model, max_batch_total_tokens),
    }
