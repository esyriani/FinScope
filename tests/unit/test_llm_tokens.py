"""Tests for LLM token estimation helpers."""

from finance_app.modules.categories import llm_tokens


class CharacterEncoding:
    """Deterministic encoding that counts one token per character."""

    name = "character-test"

    def encode(self, value):
        """Return one fake token for each character."""
        return list(str(value))


def test_estimate_llm_chat_tokens_counts_chat_message_overhead():
    """Verify token estimation includes chat wrappers and output allowance."""
    messages = [
        {"role": "system", "content": "abc"},
        {"role": "user", "content": "de", "name": "runner"},
    ]

    estimate = llm_tokens.estimate_llm_chat_tokens(
        messages,
        "gpt-test",
        expected_output_tokens=7,
        encoding_factory=lambda _model: CharacterEncoding(),
    )

    assert estimate.model == "gpt-test"
    assert estimate.input_tokens == 31
    assert estimate.expected_output_tokens == 7
    assert estimate.total_tokens == 38
    assert estimate.message_count == 2
    assert estimate.tokenizer == "character-test"
    assert estimate.tokenizer_available is True
    assert estimate.warning is None


def test_estimate_llm_chat_tokens_falls_back_when_tokenizer_is_unavailable():
    """Verify missing tokenizers return an approximate estimate with metadata."""

    def missing_encoding(_model):
        raise ImportError("missing")

    estimate = llm_tokens.estimate_llm_chat_tokens(
        [{"role": "system", "content": "abcdef"}],
        "gpt-test",
        expected_output_tokens=-4,
        encoding_factory=missing_encoding,
    )

    assert estimate.input_tokens == 10
    assert estimate.expected_output_tokens == 0
    assert estimate.total_tokens == 10
    assert estimate.tokenizer == llm_tokens.FALLBACK_ENCODING_NAME
    assert estimate.tokenizer_available is False
    assert "tiktoken is not installed" in str(estimate.warning)


def test_estimate_llm_chat_tokens_falls_back_when_encoding_load_fails():
    """Verify tokenizer load failures use the approximate estimator."""

    def failing_encoding(_model):
        raise RuntimeError("network blocked")

    estimate = llm_tokens.estimate_llm_chat_tokens(
        [{"role": "user", "content": "abcdefghi"}],
        "gpt-test",
        expected_output_tokens=3,
        encoding_factory=failing_encoding,
    )

    assert estimate.input_tokens == 10
    assert estimate.expected_output_tokens == 3
    assert estimate.total_tokens == 13
    assert estimate.tokenizer == llm_tokens.FALLBACK_ENCODING_NAME
    assert estimate.tokenizer_available is False
    assert "tiktoken encoding is unavailable" in str(estimate.warning)
