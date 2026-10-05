"""Prompt-injection defenses for LLM categorization.

This module owns local, deterministic checks around FinScope's LLM prompts and
responses. The helpers treat imported merchant text, rule text, taxonomy
instructions, and model explanations as untrusted data so provider output can be
validated without depending on model obedience.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

LLM_PROMPT_SECURITY_VERSION = "prompt-injection-v1"
FILTERED_LLM_TEXT = "[filtered instruction-like text]"
FILTERED_LLM_OUTPUT = "[filtered suspicious model output]"
MAX_LLM_DATA_TEXT_LENGTH = 240
MAX_LLM_REASON_LENGTH = 240

SECURITY_FLAG_CONTROL_CHARS = "control_characters"
SECURITY_FLAG_PROMPT_INJECTION = "prompt_injection"
SECURITY_FLAG_OBFUSCATED_PROMPT_INJECTION = "obfuscated_prompt_injection"
SECURITY_FLAG_ENCODED_PROMPT_INJECTION = "encoded_prompt_injection"
SECURITY_FLAG_MARKUP_PAYLOAD = "markup_payload"
SECURITY_FLAG_SYSTEM_PROMPT_LEAK = "system_prompt_leak"
SECURITY_FLAG_TRUNCATED_TEXT = "truncated_text"

REVIEW_SECURITY_FLAGS = frozenset(
    {
        SECURITY_FLAG_PROMPT_INJECTION,
        SECURITY_FLAG_OBFUSCATED_PROMPT_INJECTION,
        SECURITY_FLAG_ENCODED_PROMPT_INJECTION,
        SECURITY_FLAG_MARKUP_PAYLOAD,
        SECURITY_FLAG_SYSTEM_PROMPT_LEAK,
    }
)
TRANSIENT_TRANSACTION_SECURITY_KEYS = ("llm_security_flags", "llm_sanitized_fields")

CONTROL_OR_INVISIBLE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
WHITESPACE_RE = re.compile(r"\s+")
BASE64_TOKEN_RE = re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b")
WORD_RE = re.compile(r"\b[A-Za-z]{4,}\b")
INSTRUCTION_START_RE = re.compile(
    r"\b(ignore|disregard|forget|override|bypass|reveal|show|print|leak|exfiltrate|jailbreak)\b",
    re.IGNORECASE,
)
PROMPT_INJECTION_PATTERNS = (
    re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,80}"
        r"\b(previous|prior|above|earlier|all)\b.{0,40}\b(instructions?|rules?|prompt|system)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"\b(system|developer)\s*(prompt|message|instructions?)\b", re.IGNORECASE),
    re.compile(
        r"\b(reveal|show|print|leak|exfiltrate)\b.{0,60}"
        r"\b(system|developer|prompt|instructions?|api\s*key|secret)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"\b(developer\s+mode|jailbreak|prompt\s+injection|DAN)\b", re.IGNORECASE),
)
MARKUP_PAYLOAD_PATTERNS = (
    re.compile(r"<\s*(script|iframe|img|svg|object|embed|meta)\b", re.IGNORECASE),
    re.compile(r"!\[[^\]]*\]\([^)]*\)", re.IGNORECASE),
    re.compile(r"\b(javascript|data)\s*:", re.IGNORECASE),
)
SYSTEM_LEAK_PATTERNS = (
    re.compile(r"\b(system|developer)\s+(prompt|message|instructions?)\b", re.IGNORECASE),
    re.compile(r"\b(hidden|internal)\s+(prompt|policy|instructions?)\b", re.IGNORECASE),
)
TYPOGLYCEMIA_WORDS = (
    "ignore",
    "bypass",
    "reveal",
    "system",
    "prompt",
    "override",
    "instruction",
    "instructions",
)
TYPOGLYCEMIA_CONTEXT_WORDS = {"previous", "prior", "system", "prompt", "instruction", "instructions", "rules"}
DISALLOWED_PROMPT_KEYS = frozenset(
    {
        "account_id",
        "account_name",
        "account_type",
        "amount",
        "date",
        "full_description",
        "raw_description",
        "similar_transaction_examples",
        "tx_date",
    }
)


@dataclass(frozen=True)
class LlmTextSecurityReview:
    """Represent sanitized text and prompt-injection findings."""

    text: str
    flags: tuple[str, ...] = ()
    sanitized: bool = False

    @property
    def requires_review(self) -> bool:
        """Return whether flags should prevent no-review AI acceptance."""
        return llm_security_flags_require_review(self.flags)


def normalize_llm_text(value: object) -> tuple[str, bool]:
    """Return normalized text and whether unsafe control characters were removed."""
    raw_text = unicodedata.normalize("NFKC", str(value or ""))
    cleaned = CONTROL_OR_INVISIBLE_RE.sub(" ", raw_text)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned, cleaned != raw_text.strip()


def sanitize_llm_data_text(
    value: object,
    *,
    max_length: int = MAX_LLM_DATA_TEXT_LENGTH,
    replacement: str = FILTERED_LLM_TEXT,
) -> LlmTextSecurityReview:
    """Return data text safe to embed in an LLM prompt."""
    text, removed_control = normalize_llm_text(value)
    flags = list(detect_prompt_injection_text(text))
    if removed_control:
        flags.append(SECURITY_FLAG_CONTROL_CHARS)

    sanitized = removed_control
    if llm_security_flags_require_review(flags):
        text = redact_instruction_like_text(text, replacement)
        sanitized = True

    if len(text) > max_length:
        text = text[:max_length].rstrip()
        flags.append(SECURITY_FLAG_TRUNCATED_TEXT)
        sanitized = True

    return LlmTextSecurityReview(text=text, flags=unique_flags(flags), sanitized=sanitized)


def sanitize_llm_reason(value: object, *, max_length: int = MAX_LLM_REASON_LENGTH) -> LlmTextSecurityReview:
    """Return a bounded, display-safe model explanation."""
    text, removed_control = normalize_llm_text(value)
    flags = list(detect_prompt_injection_text(text))
    flags.extend(detect_system_prompt_leakage(text))
    if removed_control:
        flags.append(SECURITY_FLAG_CONTROL_CHARS)

    sanitized = removed_control
    if llm_security_flags_require_review(flags):
        text = FILTERED_LLM_OUTPUT
        sanitized = True

    if len(text) > max_length:
        text = text[:max_length].rstrip()
        flags.append(SECURITY_FLAG_TRUNCATED_TEXT)
        sanitized = True

    return LlmTextSecurityReview(text=text, flags=unique_flags(flags), sanitized=sanitized)


def detect_prompt_injection_text(value: object) -> tuple[str, ...]:
    """Return security flags for instruction-like text embedded in data."""
    text, _ = normalize_llm_text(value)
    if not text:
        return ()

    flags: list[str] = []
    if any(pattern.search(text) for pattern in PROMPT_INJECTION_PATTERNS):
        flags.append(SECURITY_FLAG_PROMPT_INJECTION)
    if any(pattern.search(text) for pattern in MARKUP_PAYLOAD_PATTERNS):
        flags.append(SECURITY_FLAG_MARKUP_PAYLOAD)
    if has_obfuscated_prompt_injection(text):
        flags.append(SECURITY_FLAG_OBFUSCATED_PROMPT_INJECTION)
    if has_encoded_prompt_injection(text):
        flags.append(SECURITY_FLAG_ENCODED_PROMPT_INJECTION)
    return unique_flags(flags)


def detect_system_prompt_leakage(value: object) -> tuple[str, ...]:
    """Return flags for model output that appears to reveal protected prompt text."""
    text, _ = normalize_llm_text(value)
    if not text:
        return ()
    return (
        (SECURITY_FLAG_SYSTEM_PROMPT_LEAK,)
        if any(pattern.search(text) for pattern in SYSTEM_LEAK_PATTERNS)
        else ()
    )


def has_obfuscated_prompt_injection(text: str) -> bool:
    """Return whether text contains simple typoglycemia variants of attack words."""
    words = [match.group(0).casefold() for match in WORD_RE.finditer(text)]
    word_set = set(words)
    if not word_set & TYPOGLYCEMIA_CONTEXT_WORDS:
        return False
    return any(
        token != target and typoglycemia_match(token, target)
        for token in words
        for target in TYPOGLYCEMIA_WORDS
    )


def typoglycemia_match(token: str, target: str) -> bool:
    """Return whether a token preserves the first/last letters of a target word."""
    return (
        len(token) == len(target)
        and len(token) >= 5
        and token[0] == target[0]
        and token[-1] == target[-1]
        and sorted(token[1:-1]) == sorted(target[1:-1])
    )


def has_encoded_prompt_injection(text: str) -> bool:
    """Return whether base64-looking text decodes to instruction-like content."""
    for match in BASE64_TOKEN_RE.finditer(text):
        token = match.group(0)
        padded = token + "=" * (-len(token) % 4)
        try:
            decoded = base64.b64decode(padded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            continue
        if any(pattern.search(decoded) for pattern in PROMPT_INJECTION_PATTERNS):
            return True
    return False


def redact_instruction_like_text(text: str, replacement: str) -> str:
    """Remove likely instructions while preserving any merchant prefix."""
    match = INSTRUCTION_START_RE.search(text)
    if match:
        prefix = text[: match.start()].strip(" -:;,.")
        return f"{prefix} {replacement}".strip() if prefix else replacement

    for match in WORD_RE.finditer(text):
        token = match.group(0).casefold()
        if any(token != target and typoglycemia_match(token, target) for target in TYPOGLYCEMIA_WORDS):
            prefix = text[: match.start()].strip(" -:;,.")
            return f"{prefix} {replacement}".strip() if prefix else replacement

    return replacement


def append_llm_security_review(
    target: Mapping[str, Any],
    review: LlmTextSecurityReview,
    field_name: str,
) -> None:
    """Attach security flags to mutable transaction payloads."""
    if not review.flags or not isinstance(target, MutableMapping):
        return

    flags = list(target.get("llm_security_flags") or [])
    fields = list(target.get("llm_sanitized_fields") or [])
    for flag in review.flags:
        if flag not in flags:
            flags.append(flag)
    if review.sanitized and field_name not in fields:
        fields.append(field_name)
    target["llm_security_flags"] = flags
    if fields:
        target["llm_sanitized_fields"] = fields


def llm_transaction_security_flags(transaction: Mapping[str, Any]) -> tuple[str, ...]:
    """Return security flags attached to one LLM transaction payload."""
    flags = transaction.get("llm_security_flags") or ()
    return tuple(str(flag) for flag in flags if flag)


def llm_transaction_sanitized_fields(transaction: Mapping[str, Any]) -> tuple[str, ...]:
    """Return prompt fields sanitized before an LLM request."""
    fields = transaction.get("llm_sanitized_fields") or ()
    return tuple(str(field) for field in fields if field)


def llm_security_flags_require_review(flags: Sequence[str]) -> bool:
    """Return whether security flags must force manual review."""
    return bool(set(flags) & REVIEW_SECURITY_FLAGS)


def unique_flags(flags: Sequence[str]) -> tuple[str, ...]:
    """Return flags without duplicates while preserving order."""
    unique: list[str] = []
    for flag in flags:
        if flag and flag not in unique:
            unique.append(flag)
    return tuple(unique)


def validate_llm_messages_security(messages: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
    """Validate final chat messages before an external provider request."""
    issues: list[str] = []
    if len(messages) != 2:
        issues.append("unexpected_message_count")
        return tuple(issues)

    roles = [message.get("role") for message in messages]
    if roles != ["system", "user"]:
        issues.append("unexpected_message_roles")

    system_content = messages[0].get("content") or ""
    if LLM_PROMPT_SECURITY_VERSION not in system_content:
        issues.append("missing_security_prompt_version")
    if "untrusted data" not in system_content:
        issues.append("missing_untrusted_data_boundary")

    try:
        payload = json.loads(messages[1].get("content") or "")
    except json.JSONDecodeError:
        issues.append("user_prompt_not_json")
        return tuple(issues)

    if not isinstance(payload, dict):
        issues.append("user_prompt_not_object")
        return tuple(issues)
    if payload.get("security", {}).get("prompt_security_version") != LLM_PROMPT_SECURITY_VERSION:
        issues.append("missing_payload_security_version")
    if not isinstance(payload.get("transactions"), list):
        issues.append("missing_transactions")
    if not isinstance(payload.get("taxonomy"), dict):
        issues.append("missing_taxonomy")

    disallowed_keys = sorted(find_disallowed_prompt_keys(payload))
    issues.extend(f"disallowed_prompt_key:{key}" for key in disallowed_keys)
    return tuple(issues)


def find_disallowed_prompt_keys(value: object) -> set[str]:
    """Return privacy-sensitive keys found in a prompt payload."""
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text in DISALLOWED_PROMPT_KEYS:
                found.add(key_text)
            found.update(find_disallowed_prompt_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(find_disallowed_prompt_keys(child))
    return found


def cleanup_llm_security_fields(transactions: Sequence[MutableMapping[str, Any]]) -> None:
    """Remove transient prompt-security fields from prepared transactions."""
    for tx in transactions:
        for key in TRANSIENT_TRANSACTION_SECURITY_KEYS:
            tx.pop(key, None)
