"""Tests for LLM token-estimate presentation helpers."""

from finance_app.modules.categories.llm_token_presenter import localize_token_estimate_result


def test_localize_token_estimate_result_translates_messages_and_warnings():
    """Verify token-estimate JSON has all browser-facing text translated."""
    translations = {
        "AI usage estimate ready.": "estimate ready translated",
        "tiktoken encoding is unavailable; using an approximate estimate.": "aggregate warning translated",
        "tiktoken is not installed; using an approximate estimate.": "batch warning translated",
    }

    result = localize_token_estimate_result(
        {
            "ok": True,
            "message": "AI usage estimate ready.",
            "estimate": {
                "warning": "tiktoken encoding is unavailable; using an approximate estimate.",
                "batches": [
                    {
                        "warning": "tiktoken is not installed; using an approximate estimate.",
                    }
                ],
            },
        },
        lambda message: translations.get(message, message),
    )

    assert result["message"] == "estimate ready translated"
    assert result["estimate"]["warning"] == "aggregate warning translated"
    assert result["estimate"]["batches"][0]["warning"] == "batch warning translated"
