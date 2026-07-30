"""Tests for LLM token-estimate aggregation helpers."""

from finance_app.modules.categories import llm_estimation


def test_summarize_llm_token_estimates_aggregates_largest_batch_without_context_limit():
    """Verify usage summaries do not assert provider context-window limits."""
    estimate = llm_estimation.summarize_llm_token_estimates(
        "gpt-4o-mini",
        2,
        [
            {
                "request_count": 1,
                "input_tokens": 50_000,
                "expected_output_tokens": 10_000,
                "total_tokens": 60_000,
                "tokenizer_available": True,
            },
            {
                "request_count": 1,
                "input_tokens": 70_000,
                "expected_output_tokens": 10_000,
                "total_tokens": 80_000,
                "tokenizer_available": True,
            },
        ],
    )

    assert estimate["total_tokens"] == 140_000
    assert estimate["max_batch_total_tokens"] == 80_000
    assert estimate["context_limit_tokens"] is None
    assert estimate["context_usage_tokens"] is None
    assert estimate["context_usage_ratio"] is None


def test_summarize_llm_token_estimates_omits_unknown_context_limit():
    """Verify custom or unknown models do not imply a misleading limit."""
    estimate = llm_estimation.summarize_llm_token_estimates(
        "custom-provider-model",
        1,
        [
            {
                "request_count": 1,
                "input_tokens": 100,
                "expected_output_tokens": 50,
                "total_tokens": 150,
                "tokenizer_available": True,
            }
        ],
    )

    assert estimate["context_limit_tokens"] is None
    assert estimate["context_usage_tokens"] is None
    assert estimate["context_usage_ratio"] is None
