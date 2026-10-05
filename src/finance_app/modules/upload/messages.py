"""Upload workflow message helpers.

Builds human-readable summaries for upload and automatic categorization jobs.
The helpers are pure formatting functions and do not access the database.
"""

import json
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from finance_app.core.constants import (
    INTERAC_IGNORED_REASON_CANCELLED,
    INTERAC_IGNORED_REASON_INVALID,
    INTERAC_IGNORED_REASON_NO_MATCH,
    INTERAC_IGNORED_REASON_NON_DEPOSITED,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
)
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


def automatic_categorization_message(
    updated_count: int,
    source_counts: Mapping[str, int] | None = None,
    unresolved_count: int | None = None,
) -> str:
    """Return a concise background-job summary for automatic categorization."""
    unresolved_suffix = unresolved_categorization_message(unresolved_count)
    if not updated_count:
        return f"0 automatically categorized.{unresolved_suffix}"

    breakdown = automatic_categorization_breakdown(source_counts or {})
    suffix = f": {breakdown}" if breakdown else ""
    return f"{updated_count} automatically categorized{suffix}.{unresolved_suffix}"


def unresolved_categorization_message(unresolved_count: int | None) -> str:
    """Return the optional unresolved-count suffix for AI categorization summaries."""
    if unresolved_count is None or unresolved_count <= 0:
        return ""
    unknown_label = "transaction" if unresolved_count == 1 else "transactions"
    return f" {unresolved_count} {unknown_label} kept unknown for review."


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
    interac_ignored_reasons: Mapping[str, int] | None = None,
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
            message += interac_ignored_message(ignored_count, interac_ignored_reasons)
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


def interac_ignored_message(ignored_count: int, reasons: Mapping[str, int] | None = None) -> str:
    """Return an Interac ignored-row message with specific reason counts."""
    counts = reasons or {}
    cancelled_count = int(counts.get(INTERAC_IGNORED_REASON_CANCELLED, 0) or 0)
    non_deposited_count = int(counts.get(INTERAC_IGNORED_REASON_NON_DEPOSITED, 0) or 0)
    no_match_count = int(counts.get(INTERAC_IGNORED_REASON_NO_MATCH, 0) or 0)
    invalid_count = int(counts.get(INTERAC_IGNORED_REASON_INVALID, 0) or 0)
    known_count = cancelled_count + non_deposited_count + no_match_count + invalid_count
    unclassified_count = max(0, ignored_count - known_count)

    ignored_label = "row" if ignored_count == 1 else "rows"
    parts = [
        f"{cancelled_count} cancelled",
        f"{non_deposited_count} non-deposited",
        f"{no_match_count} with no matching checking transaction yet",
    ]
    if invalid_count:
        parts.append(f"{invalid_count} invalid")
    if unclassified_count:
        parts.append(f"{unclassified_count} unclassified")

    message = f"Ignored {ignored_count} {ignored_label}: {', '.join(parts)}. "
    if no_match_count or unclassified_count:
        message += "Import matching checking statements first, then reprocess this Interac history. "
    return message
