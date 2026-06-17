"""Token estimation helpers for LLM categorization requests.

Counts tokens for the final chat-message payload assembled by the categories
prompt builders. The estimator prefers tiktoken when installed and falls back
to a conservative character-based estimate so previews can degrade gracefully.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CHAT_MESSAGE_OVERHEAD_TOKENS = 3
CHAT_NAME_OVERHEAD_TOKENS = 1
CHAT_REPLY_PRIMER_TOKENS = 3
DEFAULT_EXPECTED_OUTPUT_TOKENS = 512
FALLBACK_CHARS_PER_TOKEN = 4
FALLBACK_ENCODING_NAME = "character_approximation"
OPENAI_BASE_ENCODING = "o200k_base"
AI_TOKEN_ESTIMATE_CONFIRMED_FIELD = "ai_token_estimate_confirmed"
AI_TOKEN_ESTIMATE_REQUIRED_MESSAGE = "Review the estimated AI usage before continuing."
TIKTOKEN_MISSING_WARNING = "tiktoken is not installed; using an approximate estimate."
TIKTOKEN_ENCODING_UNAVAILABLE_WARNING = "tiktoken encoding is unavailable; using an approximate estimate."


@dataclass(frozen=True)
class LlmTokenEstimate:
    """Token estimate for one assembled LLM chat request."""

    model: str
    input_tokens: int
    expected_output_tokens: int
    total_tokens: int
    message_count: int
    tokenizer: str
    tokenizer_available: bool
    warning: str | None = None


def estimate_llm_chat_tokens(
    messages: Sequence[Mapping[str, object]],
    model: str,
    expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS,
    encoding_factory: Callable[[str], Any] | None = None,
) -> LlmTokenEstimate:
    """Estimate input and expected total tokens for final chat messages.

    Args:
        messages: Final OpenAI chat messages, including system and user
            content.
        model: OpenAI model name used for tokenizer selection.
        expected_output_tokens: Planning allowance for the unknown completion.
        encoding_factory: Optional factory for tests or alternate tokenizers.

    Returns:
        A token estimate with tokenizer metadata and any fallback warning.
    """
    normalized_output_tokens = max(0, int(expected_output_tokens))
    try:
        encoding = encoding_factory(model) if encoding_factory else load_tiktoken_encoding(model)
    except ImportError:
        return fallback_llm_chat_token_estimate(
            messages,
            model,
            normalized_output_tokens,
            TIKTOKEN_MISSING_WARNING,
        )
    except (OSError, RuntimeError, ValueError):
        return fallback_llm_chat_token_estimate(
            messages,
            model,
            normalized_output_tokens,
            TIKTOKEN_ENCODING_UNAVAILABLE_WARNING,
        )

    input_tokens = count_chat_message_tokens(messages, encoding)
    return LlmTokenEstimate(
        model=model,
        input_tokens=input_tokens,
        expected_output_tokens=normalized_output_tokens,
        total_tokens=input_tokens + normalized_output_tokens,
        message_count=len(messages),
        tokenizer=encoding_name(encoding),
        tokenizer_available=True,
    )


def fallback_llm_chat_token_estimate(
    messages: Sequence[Mapping[str, object]],
    model: str,
    expected_output_tokens: int,
    warning: str,
) -> LlmTokenEstimate:
    """Return an approximate estimate when a tokenizer cannot be loaded."""
    input_tokens = estimate_chat_tokens_by_characters(messages)
    return LlmTokenEstimate(
        model=model,
        input_tokens=input_tokens,
        expected_output_tokens=expected_output_tokens,
        total_tokens=input_tokens + expected_output_tokens,
        message_count=len(messages),
        tokenizer=FALLBACK_ENCODING_NAME,
        tokenizer_available=False,
        warning=warning,
    )


def load_tiktoken_encoding(model: str) -> Any:
    """Return a tiktoken encoding for a model, falling back to a base encoding."""
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding(OPENAI_BASE_ENCODING)


def count_chat_message_tokens(messages: Sequence[Mapping[str, object]], encoding: Any) -> int:
    """Count tokens for OpenAI chat messages using a tiktoken-like encoding."""
    tokens = CHAT_REPLY_PRIMER_TOKENS
    for message in messages:
        tokens += CHAT_MESSAGE_OVERHEAD_TOKENS
        tokens += len(encoding.encode(str(message.get("role") or "")))
        tokens += len(encoding.encode(str(message.get("content") or "")))
        name = message.get("name")
        if name:
            tokens += CHAT_NAME_OVERHEAD_TOKENS + len(encoding.encode(str(name)))
    return tokens


def estimate_chat_tokens_by_characters(messages: Sequence[Mapping[str, object]]) -> int:
    """Return a conservative estimate when a model tokenizer is unavailable."""
    tokens = CHAT_REPLY_PRIMER_TOKENS
    for message in messages:
        tokens += CHAT_MESSAGE_OVERHEAD_TOKENS
        tokens += approximate_text_tokens(message.get("role"))
        tokens += approximate_text_tokens(message.get("content"))
        if message.get("name"):
            tokens += CHAT_NAME_OVERHEAD_TOKENS + approximate_text_tokens(message.get("name"))
    return tokens


def approximate_text_tokens(value: object) -> int:
    """Approximate tokens from text length using a simple character ratio."""
    text = str(value or "")
    if not text:
        return 0
    return max(1, (len(text) + FALLBACK_CHARS_PER_TOKEN - 1) // FALLBACK_CHARS_PER_TOKEN)


def encoding_name(encoding: Any) -> str:
    """Return a display name for a tiktoken-like encoding object."""
    return str(getattr(encoding, "name", "") or type(encoding).__name__)
