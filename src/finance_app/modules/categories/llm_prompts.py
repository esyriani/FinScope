"""LLM categorization prompt builders.

Builds system instructions and JSON request payloads for OpenAI categorization
requests. Static system-prompt policy lives in a structured JSON resource beside
this module; runtime code loads and renders it with taxonomy rows, rule
metadata, and settings.
"""

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any

from finance_app.core.config import settings
from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_SOURCE_MANUAL,
)
from finance_app.core.money import MoneyValue, money_to_decimal
from finance_app.modules.categories.decision import HIGH_CONFIDENCE_THRESHOLD
from finance_app.modules.categories.llm_taxonomy import semantic_tokens
from finance_app.modules.categories.repository import normalize_category
from finance_app.modules.categories.taxonomy import normalize_tag_names
from finance_app.modules.merchants.normalization import normalize_merchant_description

MAX_PROMPT_MANUAL_RULES = 50
LLM_SYSTEM_PROMPT_PATH = Path(__file__).with_name("llm_system_prompt.json")


def build_llm_system_prompt(
    category_rows: Sequence[Mapping[str, Any]] | None = None,
    tag_rows: Sequence[Mapping[str, Any]] | None = None,
    verify_threshold: float | None = None,
    review_threshold: float | None = None,
) -> str:
    """Build LLM categorization policy instructions.

    Args:
        category_rows: Reserved for future taxonomy-aware system prompt sections.
        tag_rows: Reserved for future taxonomy-aware system prompt sections.
        verify_threshold: Confidence value that allows a no-review LLM result.
        review_threshold: Minimum confidence for preserving a reviewable LLM
            suggestion instead of falling back to UNKNOWN.

    Returns:
        A rendered system prompt string assembled from the structured prompt
        resource.
    """
    if verify_threshold is None:
        verify_threshold = HIGH_CONFIDENCE_THRESHOLD
    if review_threshold is None:
        review_threshold = settings.default_llm_review_threshold
    del category_rows, tag_rows

    context = {
        "verify_threshold": f"{verify_threshold:.2f}",
        "review_threshold": f"{review_threshold:.2f}",
    }
    return render_llm_system_prompt(load_llm_system_prompt_spec(), context)


@lru_cache(maxsize=1)
def load_llm_system_prompt_spec() -> dict[str, Any]:
    """Load and validate the structured LLM system-prompt resource.

    Returns:
        A dictionary containing the prompt role, ordered sections, and output
        schema.

    Raises:
        ValueError: If the resource is missing the required structure.
    """
    with LLM_SYSTEM_PROMPT_PATH.open(encoding="utf-8") as prompt_file:
        prompt_spec = json.load(prompt_file)
    validate_llm_system_prompt_spec(prompt_spec)
    return prompt_spec


def validate_llm_system_prompt_spec(prompt_spec: object) -> None:
    """Validate the minimal structure required to render a system prompt.

    Args:
        prompt_spec: Parsed prompt resource dictionary.

    Raises:
        ValueError: If required prompt fields are absent or malformed.
    """
    if not isinstance(prompt_spec, dict):
        raise ValueError("LLM system prompt resource must be a JSON object.")
    if not prompt_spec.get("role"):
        raise ValueError("LLM system prompt resource must define a role.")
    sections = prompt_spec.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("LLM system prompt resource must define ordered sections.")
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict) or not section.get("heading"):
            raise ValueError(f"LLM system prompt section {index} must define a heading.")
    if not prompt_spec.get("output_schema"):
        raise ValueError("LLM system prompt resource must define an output schema.")


def render_llm_system_prompt(prompt_spec: Mapping[str, Any], context: Mapping[str, str]) -> str:
    """Render a prompt specification into the final system prompt text.

    Args:
        prompt_spec: Structured system-prompt resource.
        context: Template values for runtime threshold placeholders.

    Returns:
        The final prompt text sent as the OpenAI system message.
    """
    lines = [render_prompt_text(prompt_spec["role"], context), ""]
    for section in prompt_spec["sections"]:
        lines.extend(render_prompt_section(section, context))
        lines.append("")

    output_intro = prompt_spec.get("output_intro")
    if output_intro:
        lines.append(render_prompt_text(output_intro, context))
    lines.append(json.dumps(prompt_spec["output_schema"], ensure_ascii=True, indent=2))
    return "\n".join(lines).rstrip()


def render_prompt_section(section: Mapping[str, Any], context: Mapping[str, str]) -> list[str]:
    """Render one named prompt section from paragraphs, bullets, and rules.

    Args:
        section: Structured section dictionary from the prompt resource.
        context: Template values for runtime threshold placeholders.

    Returns:
        A list of rendered prompt lines.
    """
    lines = [f"{section['heading']}:"]
    for paragraph in section.get("paragraphs", ()):
        lines.append(render_prompt_text(paragraph, context))
    for bullet in section.get("bullets", ()):
        lines.append(f"- {render_prompt_text(bullet, context)}")
    for index, rule in enumerate(section.get("numbered", ()), start=1):
        lines.append(f"{index}. {render_prompt_text(rule, context)}")
    return lines


def render_prompt_text(text: object, context: Mapping[str, str]) -> str:
    """Apply simple placeholder substitution to prompt text.

    Args:
        text: Prompt text containing optional ``${name}`` placeholders.
        context: Mapping of placeholder names to string values.

    Returns:
        Rendered text with runtime values substituted.
    """
    return Template(str(text)).safe_substitute(context)


def taxonomy_prompt_line(row: Mapping[str, Any]) -> str:
    """Render a taxonomy row as a compact prompt line for compatibility callers."""
    detail = row["instruction"] or row["description"]
    label = f"ID {row.get('id')}: {row['name']}"
    return f"- {label}: {detail}" if detail else f"- {label}"


def build_llm_prompt(
    unknown_items: Sequence[Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
    category_options: Sequence[str],
    tag_options: Sequence[str] | None = None,
    category_rows: Sequence[Mapping[str, Any]] | None = None,
    tag_rows: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Build the LLM request payload with full taxonomy and compact candidates.

    Transaction payloads are privacy-minimized before they are serialized for an
    external model request. Raw statement descriptions, dates, account identity,
    exact amounts, and similar-transaction examples stay local to FinScope.
    """
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
            "direction": rule.get("direction") or CATEGORY_RULE_DIRECTION_ANY,
        }
        for rule in prompt_relevant_manual_rules(rules, unknown_items)
        if (
            rule["source"] == CATEGORY_RULE_SOURCE_MANUAL
            and normalize_category(rule["category"], category_options) in category_options
        )
    ]

    payload = {
        "taxonomy": {
            "categories": taxonomy_payload_rows(category_options, category_rows),
            "tags": taxonomy_payload_rows(tag_options, tag_rows),
        },
        "examples": examples,
        "current_manual_rules": manual_rules,
        "rule_matching": (
            "Manual rules match by normalized keyword containment. "
            "If direction is present, the signed transaction direction must match. "
            "Negative amounts are credits/income/refunds."
        ),
        "transactions": [
            transaction_prompt_payload(tx, category_options, tag_options, category_rows, tag_rows)
            for tx in unknown_items
        ],
        "matching_rule": "Return one result per transaction. Copy request_id exactly from each input transaction.",
        "required_schema": {
            "results": [
                {
                    "request_id": "same request_id as the input transaction",
                    "category_id": "one category id from taxonomy.categories",
                    "tag_ids": ["zero or more tag ids from taxonomy.tags"],
                    "confidence": 0.0,
                    "needs_review": True,
                    "supported_by_similar_transactions": False,
                    "reason": "short explanation",
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def transaction_prompt_payload(
    tx: Mapping[str, Any],
    category_options: Sequence[str],
    tag_options: Sequence[str],
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a privacy-minimized transaction payload for the LLM prompt."""
    return {
        "request_id": tx.get("llm_request_id"),
        "merchant_key": normalize_merchant_description(tx.get("merchant_key") or ""),
        "amount_direction": amount_direction(tx.get("amount")),
        "amount_magnitude": amount_magnitude(tx.get("amount")),
        "transaction_kind": tx.get("transaction_kind"),
        "evidence_summary": evidence_summary(
            tx.get("rule_evidence"),
            tx.get("historical_evidence"),
        ),
        "candidate_taxonomy": {
            "categories": taxonomy_reference_rows(
                tx.get("llm_candidate_categories") or category_options,
                category_rows,
            ),
            "tags": taxonomy_reference_rows(
                tx.get("llm_candidate_tags") or tag_options,
                tag_rows,
            ),
        },
        "metadata": {
            "current_category": tx.get("category"),
        },
    }


def amount_direction(value: MoneyValue | None) -> str:
    """Return a coarse signed amount direction for prompt context."""
    if value is None:
        return "unknown"
    amount = money_to_decimal(value)
    if amount > 0:
        return "debit"
    if amount < 0:
        return "credit"
    return "zero"


def amount_magnitude(value: MoneyValue | None) -> str:
    """Return a coarse amount bucket without exposing the exact amount."""
    if value is None:
        return "unknown"

    absolute_amount = abs(money_to_decimal(value))
    if absolute_amount < 20:
        return "small"
    if absolute_amount < 100:
        return "medium"
    if absolute_amount < 500:
        return "large"
    return "very_large"


def evidence_summary(rule_evidence: Any, historical_evidence: Any) -> dict[str, Any]:
    """Return category evidence stripped of transaction-identifying details."""
    return {
        "best_matching_rule": compact_evidence(rule_evidence),
        "similar_transactions": compact_evidence(historical_evidence),
    }


def compact_evidence(evidence: Any) -> dict[str, Any] | None:
    """Return category, tags, and confidence from local evidence only."""
    if not evidence:
        return None
    return {
        "category": evidence.get("category"),
        "tags": list(evidence.get("tags") or []),
        "confidence": evidence.get("confidence"),
    }


def prompt_relevant_manual_rules(
    rules: Sequence[Mapping[str, Any]],
    unknown_items: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Return manual rules most relevant to the current LLM batch."""
    scored_rules: list[tuple[int, int, Mapping[str, Any]]] = []
    for index, rule in enumerate(rules):
        if rule["source"] != CATEGORY_RULE_SOURCE_MANUAL:
            continue
        score = manual_rule_prompt_relevance(rule, unknown_items)
        if score:
            scored_rules.append((score, index, rule))

    scored_rules.sort(key=lambda item: (-item[0], item[1]))
    return [rule for _, _, rule in scored_rules[:MAX_PROMPT_MANUAL_RULES]]


def manual_rule_prompt_relevance(rule: Mapping[str, Any], unknown_items: Sequence[Mapping[str, Any]]) -> int:
    """Return a lightweight relevance score for sending a manual rule."""
    keyword = normalize_merchant_description(rule["keyword"])
    if not keyword:
        return 0

    keyword_tokens = semantic_tokens(keyword)
    best_score = 0
    for tx in unknown_items:
        candidate = normalize_merchant_description(
            " ".join(
                str(value or "")
                for value in (
                    tx.get("merchant_key"),
                    tx.get("description"),
                )
            )
        )
        if not candidate:
            continue
        if keyword in candidate or candidate in keyword:
            best_score = max(best_score, 100)
            continue
        overlap = keyword_tokens & semantic_tokens(candidate)
        if overlap:
            best_score = max(best_score, len(overlap))

    return best_score


def taxonomy_reference_rows(names: Sequence[str], taxonomy_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return compact taxonomy rows for transaction-local candidate hints."""
    rows_by_name = {row["name"]: row for row in taxonomy_rows}
    payload: list[dict[str, Any]] = []
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


def taxonomy_payload_rows(names: Sequence[str], taxonomy_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return compact taxonomy metadata for prompt payloads."""
    rows_by_name = {row["name"]: row for row in taxonomy_rows}
    payload: list[dict[str, Any]] = []
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


def build_rule_examples(rules: Sequence[Mapping[str, Any]], category_options: Sequence[str]) -> dict[str, Any]:
    """Return the legacy prompt examples payload for supported rule sources.

    Manual rules are sent through `current_manual_rules`, so this compatibility
    payload remains intentionally empty.
    """
    return {}
