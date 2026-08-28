"""Upload workflow message helpers.

Builds human-readable summaries for upload and automatic categorization jobs.
The helpers are pure formatting functions and do not access the database.
"""

import json
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from finance_app.core.constants import STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_RULE,
)

AUTOMATIC_CATEGORIZATION_SOURCE_ORDER = (
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_RULE,
)
AUTOMATIC_CATEGORIZATION_SOURCE_LABELS = {
    CATEGORY_SOURCE_HISTORY: "similarity",
    CATEGORY_SOURCE_AI: "AI",
    CATEGORY_SOURCE_RULE: "rule",
}


def ai_batch_report(
    categorized: Sequence[Mapping[str, Any]], request_status: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Return concise AI request and unresolved-result details for one batch."""
    failure_counts = llm_failure_counts(categorized)
    return {
        "request_status": dict(request_status or {}),
        "failure_counts": failure_counts,
        "unknown_count": sum(failure_counts.values()),
    }


def llm_failure_counts(transactions: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count LLM failure reasons from categorized transaction metadata."""
    counts: dict[str, int] = {}
    for tx in transactions:
        metadata = transaction_category_metadata(tx)
        reason = metadata.get("failure_reason")
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def transaction_category_metadata(transaction: Mapping[str, Any]) -> dict[str, Any]:
    """Return category metadata as a dictionary when available."""
    metadata = transaction.get("category_metadata")
    if not metadata:
        return {}
    if isinstance(metadata, dict):
        return metadata
    try:
        parsed = json.loads(metadata)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def ai_request_status_needs_log(status: Mapping[str, Any] | None) -> bool:
    """Return whether an LLM request status should be surfaced in the job log."""
    if not status:
        return False
    return status.get("status") not in {"ok", "not_requested"}


def format_failure_counts(counts: Mapping[str, int]) -> str:
    """Return compact failure reason counts for progress logs."""
    return ", ".join(f"{reason}: {count}" for reason, count in sorted(counts.items()))


def merge_source_counts(target: MutableMapping[str, int], source: Mapping[str, int]) -> None:
    """Add source-count values into an aggregate dictionary."""
    for key, value in source.items():
        target[key] = target.get(key, 0) + value


def automatic_categorization_message(updated_count: int, source_counts: Mapping[str, int] | None = None) -> str:
    """Return a concise background-job summary for automatic categorization."""
    if not updated_count:
        return "0 automatically categorized."

    breakdown = automatic_categorization_breakdown(source_counts or {})
    suffix = f": {breakdown}" if breakdown else ""
    return f"{updated_count} automatically categorized{suffix}."


def automatic_categorization_breakdown(source_counts: Mapping[str, int]) -> str:
    """Return a stable source-count breakdown for automatic categorization."""
    parts: list[str] = []
    seen: set[str] = set()
    for source in AUTOMATIC_CATEGORIZATION_SOURCE_ORDER:
        count = source_counts.get(source, 0)
        if count:
            parts.append(f"{count} {AUTOMATIC_CATEGORIZATION_SOURCE_LABELS[source]}")
            seen.add(source)

    for source in sorted(set(source_counts) - seen):
        count = source_counts[source]
        if count:
            label = AUTOMATIC_CATEGORIZATION_SOURCE_LABELS.get(source, str(source or "other"))
            parts.append(f"{count} {label}")

    return ", ".join(parts)


def upload_result_message(
    statement_type: str,
    extension: str,
    inserted_count: int,
    skipped_count: int,
    ignored_count: int,
    llm_candidate_count: int = 0,
    auto_llm_job_id: str | None = None,
) -> str:
    """Render the background upload result message."""
    del extension
    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        message = f"Interac history processed. Enriched {inserted_count} existing transactions. "
        if skipped_count:
            message += (
                f"Skipped {skipped_count} ambiguous match"
                f"{'' if skipped_count == 1 else 'es'} because each matched more than one possible checking transaction. "
            )
        if ignored_count:
            ignored_label = "row" if ignored_count == 1 else "rows"
            ignored_verb = "was" if ignored_count == 1 else "were"
            message += (
                f"Ignored {ignored_count} {ignored_label} that {ignored_verb} cancelled, "
                "non-deposited, or had no matching checking ledger transaction yet. "
                "Import matching checking statements first, then reprocess this Interac history. "
            )
        message += "No duplicate Interac ledger rows were added."
        return message

    message = (
        f"Statement uploaded. Added {inserted_count} transactions. " f"Skipped {skipped_count} duplicate transactions. "
    )

    if ignored_count:
        message += f"Ignored {ignored_count} non-transaction rows. "

    if llm_candidate_count:
        transaction_label = f"unknown transaction{'' if llm_candidate_count == 1 else 's'}"
        if auto_llm_job_id:
            message += (
                f"{llm_candidate_count} {transaction_label} queued for AI categorization. "
                f"AI job: {auto_llm_job_id[:8]}. "
            )
        else:
            message += (
                f"{llm_candidate_count} {transaction_label} can be categorized with AI from Uploaded statements. "
            )

    message += "The original file was not stored."
    return message
