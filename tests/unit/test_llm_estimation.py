"""Tests for LLM token-estimate aggregation helpers."""

import pytest

from finance_app.modules.categories import llm_estimation


def test_summarize_llm_token_estimates_reports_known_context_limit():
    """Verify context usage uses the largest request, not the aggregate total."""
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
    assert estimate["context_limit_tokens"] == 128_000
    assert estimate["context_usage_tokens"] == 80_000
    assert estimate["context_usage_ratio"] == pytest.approx(80_000 / 128_000)


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


def test_model_context_limit_tokens_matches_versioned_known_models():
    """Verify versioned model ids can still use known family limits."""
    assert llm_estimation.model_context_limit_tokens("gpt-4o-mini-2024-07-18") == 128_000
    assert llm_estimation.model_context_limit_tokens("gpt-4.1-mini-2025-04-14") == 1_000_000
