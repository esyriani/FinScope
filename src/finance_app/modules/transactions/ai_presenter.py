"""AI categorization response shaping for transaction routes.

This module owns JSON-ready payloads for transaction AI modals and estimates.
It does not call external providers or mutate transaction persistence.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from finance_app.core.constants import TRANSACTION_KINDS
from finance_app.modules.categories.sources import (
    category_confidence_label,
    category_source_badge_class,
    category_source_label,
)
from finance_app.modules.rules.forms import normalize_rule_keyword


def build_transaction_ai_result(
    original_row: Mapping[str, Any],
    original_tags: Sequence[str],
    llm_transaction: Mapping[str, Any],
    request_status: Mapping[str, Any],
    tag_colors: Mapping[str, str],
    request_ok: bool,
    unknown_category: str,
    model_name: str,
) -> dict[str, Any]:
    """Build a JSON-serializable modal view model for a one-off AI run."""
    metadata = parse_ai_metadata(llm_transaction.get("category_metadata"))
    tags = list(llm_transaction.get("tags") or [])
    original_tag_list = list(original_tags or [])
    category = llm_transaction.get("category")
    can_apply = bool(request_ok and category and category != unknown_category)
    message = ai_suggestion_message(request_status, can_apply)
    amount = original_row.get("amount")
    transaction_kind = str(original_row.get("transaction_kind") or "")
    return {
        "ok": bool(request_ok),
        "applied": False,
        "can_apply": can_apply,
        "message": message,
        "transaction_id": original_row["id"],
        "description": original_row["description"],
        "account_name": original_row.get("account_name"),
        "tx_date": stringify_date(original_row.get("tx_date")),
        "amount": amount,
        "transaction_kind": transaction_kind,
        "transaction_kind_label": TRANSACTION_KINDS.get(transaction_kind, transaction_kind),
        "previous_category": original_row.get("category"),
        "previous_tags": original_tag_list,
        "previous_tag_pills": tag_pills(original_tag_list, tag_colors),
        "category": category,
        "tags": tags,
        "tag_pills": tag_pills(tags, tag_colors),
        "needs_review": bool(llm_transaction.get("needs_review")),
        "category_source": llm_transaction.get("category_source"),
        "category_source_label": category_source_label(llm_transaction.get("category_source")),
        "category_source_badge_class": category_source_badge_class(llm_transaction.get("category_source")),
        "category_confidence": llm_transaction.get("category_confidence"),
        "category_confidence_label": category_confidence_label(llm_transaction.get("category_confidence")),
        "model": model_name,
        "request_status": request_status,
        "request_status_label": request_status_label(request_status),
        "request_detail": request_status.get("detail") or "",
        "metadata_pretty": json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True) if metadata else "",
        "llm_reason": metadata.get("llm_reason") or "",
        "llm_confidence": metadata.get("llm_confidence"),
        "proposed_confidence": metadata.get("proposed_confidence"),
        "final_confidence": metadata.get("final_confidence"),
        "review_required": bool(metadata.get("review_required")),
        "failure_reason": metadata.get("failure_reason") or "",
        "supported_by_similar_transactions": metadata.get("supported_by_similar_transactions"),
        "rule_evidence": metadata.get("rule") or llm_transaction.get("rule_evidence") or {},
        "retrieval_evidence": metadata.get("retrieval") or llm_transaction.get("historical_evidence") or {},
        "rule_keyword": llm_transaction_rule_keyword(original_row),
        "rule_exact_amount": f"{amount:.2f}" if amount is not None else "",
        "persistence": ai_suggestion_persistence(llm_transaction, metadata),
    }


def ai_suggestion_persistence(llm_transaction: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return the AI suggestion fields needed for a later explicit apply."""
    return {
        "category": llm_transaction.get("category"),
        "tags": list(llm_transaction.get("tags") or []),
        "needs_review": 1 if llm_transaction.get("needs_review") else 0,
        "category_source": llm_transaction.get("category_source"),
        "category_confidence": llm_transaction.get("category_confidence"),
        "category_rule_id": llm_transaction.get("category_rule_id"),
        "category_metadata": metadata,
        "categorized_at": llm_transaction.get("categorized_at"),
        "reviewed_at": llm_transaction.get("reviewed_at"),
        "amount": llm_transaction.get("amount"),
        "transaction_kind": llm_transaction.get("transaction_kind"),
    }


def llm_transaction_rule_keyword(original_row: Mapping[str, Any]) -> str:
    """Return the default rule keyword shown with an AI suggestion."""
    return normalize_rule_keyword("", original_row.get("description", ""))


def ai_suggestion_message(request_status: Mapping[str, Any], can_apply: bool) -> str:
    """Return a user-facing message for a one-transaction AI suggestion."""
    if request_status.get("status") == "ok":
        return "AI suggestion ready." if can_apply else "AI suggestion cannot be applied."
    return ai_request_failure_message(request_status)


def disabled_ai_result() -> dict[str, Any]:
    """Return the result shown when the per-transaction AI action is disabled."""
    return {
        "ok": False,
        "applied": False,
        "message": "Single-transaction AI is disabled in settings.",
        "request_status_label": "disabled",
    }


def disabled_ai_estimate_result() -> dict[str, Any]:
    """Return the estimate result shown when single-transaction AI is disabled."""
    return {
        "ok": False,
        "message": "Single-transaction AI is disabled in settings.",
    }


def missing_transaction_ai_result(transaction_id: int) -> dict[str, Any]:
    """Return the result shown when the selected transaction no longer exists."""
    return {
        "ok": False,
        "applied": False,
        "transaction_id": transaction_id,
        "message": "Transaction not found.",
        "request_status_label": "not_found",
    }


def missing_transaction_ai_estimate_result(transaction_id: int) -> dict[str, Any]:
    """Return the estimate result shown when a transaction no longer exists."""
    return {
        "ok": False,
        "transaction_id": transaction_id,
        "message": "Transaction not found.",
    }


def disabled_ai_apply_result() -> dict[str, Any]:
    """Return the apply result shown when single-transaction AI is disabled."""
    return {
        "updated": False,
        "message": "Single-transaction AI is disabled in settings.",
    }


def missing_transaction_apply_result(transaction_id: int) -> dict[str, Any]:
    """Return the apply result shown when the selected transaction no longer exists."""
    return {
        "updated": False,
        "transaction_id": transaction_id,
        "message": "Transaction not found.",
    }


def ai_request_failure_message(request_status: Mapping[str, Any]) -> str:
    """Return a user-facing message for a request that was not persisted."""
    status = request_status.get("status")
    if status == "configuration_missing":
        return "OpenAI API key is not configured."
    if status == "dependency_missing":
        return "OpenAI package is not installed."
    if status == "not_requested":
        return "AI categorization did not run."
    return "AI categorization could not be applied."


def parse_ai_metadata(value: object) -> dict[str, Any]:
    """Return category metadata as a dictionary for the result modal."""
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def tag_pills(tags: Sequence[str], tag_colors: Mapping[str, str]) -> list[dict[str, str]]:
    """Return tag display pills for the AI result modal."""
    return [
        {
            "name": tag,
            "color": tag_colors.get(tag, "#64748b"),
        }
        for tag in tags
    ]


def request_status_label(request_status: Mapping[str, Any]) -> str:
    """Return a compact status label for the LLM request result."""
    return str(request_status.get("status") or "unknown")


def stringify_date(value: object) -> str:
    """Return a stable date string for session storage and modal rendering."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def ai_token_estimate_result(scope: str, transaction_count: int, estimate: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready token estimate payload for transaction AI actions."""
    request_count = int(estimate.get("request_count") or 0)
    return {
        "ok": True,
        "scope": scope,
        "transaction_count": transaction_count,
        "message": (
            "No AI request would be sent for this action." if request_count == 0 else "AI usage estimate ready."
        ),
        "estimate": dict(estimate),
    }
