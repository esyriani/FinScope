"""AI categorization workflows for uploaded and existing transactions.

This module owns background job queueing, progress reporting, and batched
unknown-transaction AI categorization. Statement import execution stays in
``upload.workflow`` and calls these helpers when AI follow-up work is needed.
"""

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from sqlalchemy import func, select, update

from finance_app.background.runner import (
    AI_JOB_QUEUE,
    append_background_job_log,
    is_job_cancel_requested,
    raise_if_cancel_requested,
    submit_background_job,
    update_background_job_progress,
)
from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories import llm as llm_module
from finance_app.modules.categories.llm_workflow import (
    prepare_transaction_llm_categorization,
    request_prepared_transaction_llm_categorization,
)
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.sources import CATEGORY_SOURCE_UNKNOWN, category_metadata_json
from finance_app.modules.categories.taxonomy import set_transaction_tags
from finance_app.modules.settings.runtime import confirm_ai_token_usage_enabled, get_unknown_category
from finance_app.modules.upload.messages import (
    ai_batch_report,
    ai_request_status_needs_log,
    automatic_categorization_message,
    format_failure_counts,
    merge_source_counts,
)


def should_auto_queue_statement_llm(conn: Any, llm_candidate_count: int) -> bool:
    """Return whether import should immediately queue AI for statement unknowns."""
    return bool(llm_candidate_count and not confirm_ai_token_usage_enabled(conn))


def queue_statement_llm_categorization(statement_id: int) -> str:
    """Queue AI categorization for unknown transactions from one statement."""
    return submit_background_job(
        f"AI categorize statement {statement_id}",
        categorize_statement_unknown_transactions_job,
        statement_id,
        queue=AI_JOB_QUEUE,
    )


def queue_all_unknown_llm_categorization() -> str:
    """Queue AI categorization for all current unknown transactions."""
    return submit_background_job(
        "AI categorize all unknown transactions",
        categorize_all_unknown_transactions_job,
        queue=AI_JOB_QUEUE,
    )


def count_statement_unknown_transactions(conn: Any, statement_id: int) -> int:
    """Count statement unknown transactions."""
    return count_unknown_transactions(conn, statement_id=statement_id)


def count_unknown_transactions(conn: Any, statement_id: int | None = None) -> int:
    """Count active unknown transactions, optionally scoped to one statement."""
    unknown_category = get_unknown_category(conn)
    return conn.execute(
        select(func.count().label("count"))
        .select_from(transactions_table)
        .where(*unknown_transaction_conditions(unknown_category, statement_id=statement_id))
    ).scalar_one()


def unknown_transaction_conditions(
    unknown_category: str,
    statement_id: int | None = None,
    excluded_ids: set[int] | None = None,
) -> list[Any]:
    """Return Core predicates for active transactions eligible for AI reruns."""
    conditions = [
        transactions_table.c.ignored == 0,
        transaction_category_label_expression(unknown_category) == unknown_category,
    ]
    if statement_id is not None:
        conditions.append(transactions_table.c.statement_id == statement_id)
    if excluded_ids:
        conditions.append(~transactions_table.c.id.in_(list(excluded_ids)))
    return conditions


def categorize_statement_unknown_transactions_job(
    statement_id: int,
    batch_size: int | None = None,
    transaction_categorizer: Any = None,
    row_categorizer: Any = None,
    progress_updater: Any = None,
    log_appender: Any = None,
) -> str:
    """Categorize statement unknown transactions job."""
    return categorize_unknown_transactions_job(
        statement_id=statement_id,
        batch_size=batch_size,
        transaction_categorizer=transaction_categorizer,
        row_categorizer=row_categorizer,
        progress_updater=progress_updater,
        log_appender=log_appender,
    )


def categorize_all_unknown_transactions_job() -> str:
    """Categorize all active unknown transactions with AI assistance."""
    return categorize_unknown_transactions_job(statement_id=None)


def categorize_unknown_transactions_job(
    statement_id: int | None = None,
    batch_size: int | None = None,
    transaction_categorizer: Any = None,
    row_categorizer: Any = None,
    progress_updater: Any = None,
    log_appender: Any = None,
) -> str:
    """Categorize unknown transactions in bounded, resumable AI batches.

    The job commits after each batch so previously updated transactions survive
    later timeouts, process shutdowns, or cooperative cancellation requests.
    Optional collaborators allow tests and alternate runners to inject fakes
    without replacing module globals.
    """
    batch_size = batch_size or llm_module.LLM_BATCH_SIZE
    row_categorizer = row_categorizer or categorize_unknown_transaction_rows
    processed_ids: set[int] = set()
    processed_count = 0
    updated_count = 0
    source_counts: dict[str, int] = {}
    with db_core_transaction() as conn:
        total_candidates = count_unknown_transactions(conn, statement_id=statement_id)

    if not total_candidates:
        update_ai_categorization_progress(
            0,
            0,
            0,
            log_message="No unknown transactions needed AI categorization.",
            progress_updater=progress_updater,
            log_appender=log_appender,
        )
        return "No unknown transactions needed AI categorization."

    update_ai_categorization_progress(
        0,
        total_candidates,
        0,
        log_message="Starting AI categorization for {total} unknown transactions.",
        log_params={"total": total_candidates},
        progress_updater=progress_updater,
        log_appender=log_appender,
    )

    while True:
        if is_job_cancel_requested():
            append_ai_categorization_log(
                "Cancellation requested; stopping before the next batch.",
                level="warning",
                log_appender=log_appender,
            )
        raise_if_cancel_requested("AI categorization cancelled after the current batch.")
        with db_core_transaction() as conn:
            unknown_category = get_unknown_category(conn)
            rows = unknown_transaction_rows(
                conn,
                unknown_category,
                statement_id=statement_id,
                excluded_ids=processed_ids,
                limit=batch_size,
            )

        if not rows:
            break

        batch_start = processed_count + 1
        batch_end = processed_count + len(rows)
        append_ai_categorization_log(
            "Starting batch {start}-{end} of {total}.",
            params={
                "start": batch_start,
                "end": batch_end,
                "total": total_candidates,
            },
            log_appender=log_appender,
        )
        update_ai_categorization_progress(
            processed_count,
            total_candidates,
            updated_count,
            message="Processing {start}-{end} of {total}; {updated} categorized so far.",
            params={
                "start": batch_start,
                "end": batch_end,
                "total": total_candidates,
                "updated": updated_count,
            },
            progress_updater=progress_updater,
            log_appender=log_appender,
        )
        processed_ids.update(row["id"] for row in rows)
        try:
            batch_updated_count, batch_source_counts, batch_report = row_categorizer(
                rows,
                transaction_categorizer=transaction_categorizer,
            )
        except Exception as exc:
            error_params = {
                "start": batch_start,
                "end": batch_end,
                "error_type": type(exc).__name__,
                "detail": str(exc),
            }
            append_ai_categorization_log(
                "Batch {start}-{end} failed: {error_type}: {detail}",
                params=error_params,
                level="error",
                log_appender=log_appender,
            )
            update_ai_categorization_progress(
                processed_count,
                total_candidates,
                updated_count,
                message="Batch {start}-{end} failed: {error_type}: {detail}",
                params={
                    **error_params,
                    "total": total_candidates,
                    "updated": updated_count,
                },
                progress_updater=progress_updater,
                log_appender=log_appender,
            )
            raise
        processed_count += len(rows)
        updated_count += batch_updated_count
        merge_source_counts(source_counts, batch_source_counts)
        log_ai_batch_report(
            batch_start,
            batch_end,
            len(rows),
            batch_updated_count,
            updated_count,
            batch_report,
            log_appender=log_appender,
        )
        update_ai_categorization_progress(
            processed_count,
            total_candidates,
            updated_count,
            progress_updater=progress_updater,
            log_appender=log_appender,
        )

    summary = automatic_categorization_message(updated_count, source_counts)
    append_ai_categorization_log(
        "AI categorization completed: {summary}",
        params={"summary": summary},
        log_appender=log_appender,
    )
    return summary


def update_ai_categorization_progress(
    current: int,
    total: int,
    updated: int,
    message: str | None = None,
    params: Mapping[str, Any] | None = None,
    log_message: str | None = None,
    log_params: Mapping[str, Any] | None = None,
    log_level: str = "info",
    progress_updater: Any = None,
    log_appender: Any = None,
) -> None:
    """Publish progress for the currently running AI categorization job."""
    progress_updater = progress_updater or update_background_job_progress
    default_message = "Processed {current} of {total}; {updated} categorized."
    progress_params = {
        "current": current,
        "total": total,
        "updated": updated,
    }
    if params:
        progress_params.update(params)
    progress_updater(
        current=current,
        total=total,
        message=message or default_message,
        params=progress_params,
    )
    if log_message:
        append_ai_categorization_log(
            log_message,
            params={**progress_params, **(log_params or {})},
            level=log_level,
            log_appender=log_appender,
        )


def append_ai_categorization_log(
    message: str,
    params: Mapping[str, Any] | None = None,
    level: str = "info",
    log_appender: Any = None,
) -> None:
    """Append an AI categorization log entry to the current background job."""
    log_appender = log_appender or append_background_job_log
    log_appender(message, params=params, level=level)


def categorize_unknown_transaction_rows(
    rows: Sequence[Mapping[str, Any]],
    transaction_categorizer: Any = None,
) -> tuple[int, dict[str, int], dict[str, Any]]:
    """Categorize and persist one batch of unknown transaction rows."""
    llm_module.clear_llm_request_status()
    transactions: list[MutableMapping[str, Any]] = [
        {
            "id": row["id"],
            "account_id": row["account_id"],
            "tx_date": row["tx_date"],
            "merchant_id": row["merchant_id"],
            "description": row["description"],
            "amount": row["amount"],
            "category": row["category"] or UNKNOWN_CATEGORY,
            "transaction_kind": row["transaction_kind"],
        }
        for row in rows
    ]

    if transaction_categorizer is None:
        prepared = prepare_transaction_llm_categorization(transactions)
        outcome = request_prepared_transaction_llm_categorization(prepared)
        unknown_category = prepared.unknown_category
    else:
        with db_core_transaction() as conn:
            unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
            categorized = transaction_categorizer(transactions, conn=conn, use_llm=True)
        prepared = None
        outcome = None
        transactions = list(categorized)

    with db_core_transaction() as conn:
        if prepared is not None and outcome is not None:
            llm_module.apply_llm_categorization_outcome(
                conn,
                transactions,
                outcome,
                unknown_category,
            )
        categorized = transactions
        batch_report = ai_batch_report(categorized, llm_module.last_llm_request_status())
        updated_count = 0
        source_counts: dict[str, int] = {}
        for tx in categorized:
            is_unknown_result = tx.get("category") in (None, unknown_category)
            if is_unknown_result and not tx.get("category_metadata"):
                continue

            cursor = update_unknown_transaction_category(conn, tx, unknown_category)
            if cursor.rowcount and not is_unknown_result:
                set_transaction_tags(
                    conn,
                    tx["id"],
                    tx.get("tags", []),
                    source=tx.get("category_source", CATEGORY_SOURCE_UNKNOWN),
                    rule_id=tx.get("category_rule_id"),
                )
                updated_count += 1
                source = tx.get("category_source") or CATEGORY_SOURCE_UNKNOWN
                source_counts[source] = source_counts.get(source, 0) + 1

    return updated_count, source_counts, batch_report


def log_ai_batch_report(
    batch_start: int,
    batch_end: int,
    processed: int,
    batch_updated: int,
    total_updated: int,
    report: Mapping[str, Any],
    log_appender: Any = None,
) -> None:
    """Append useful AI batch request details to the current job log."""
    request_status = report.get("request_status") or {}
    if ai_request_status_needs_log(request_status):
        append_ai_categorization_log(
            "AI request issue in batch {start}-{end}: {error_type}: {detail}",
            params={
                "start": batch_start,
                "end": batch_end,
                "error_type": request_status.get("error_type") or request_status.get("status") or "AI",
                "detail": request_status.get("detail") or "",
            },
            level="warning",
            log_appender=log_appender,
        )

    unknown_count = int(report.get("unknown_count") or 0)
    if unknown_count:
        message = (
            "Batch {start}-{end} kept {unknown} transaction unknown for review."
            if unknown_count == 1
            else "Batch {start}-{end} kept {unknown} transactions unknown for review."
        )
        append_ai_categorization_log(
            message,
            params={
                "start": batch_start,
                "end": batch_end,
                "unknown": unknown_count,
                "reasons": format_failure_counts(report.get("failure_counts") or {}),
            },
            level="warning",
            log_appender=log_appender,
        )

    append_ai_categorization_log(
        "Finished batch {start}-{end}: {processed} processed; {updated} categorized total.",
        params={
            "start": batch_start,
            "end": batch_end,
            "processed": processed,
            "updated": total_updated,
            "batch_updated": batch_updated,
        },
        log_appender=log_appender,
    )


def unknown_transaction_rows(
    conn: Any,
    unknown_category: str,
    statement_id: int | None = None,
    excluded_ids: set[int] | None = None,
    limit: int | None = None,
) -> Any:
    """Return active unknown transactions eligible for AI categorization."""
    statement = (
        select(
            transactions_table.c.id,
            transactions_table.c.account_id,
            transactions_table.c.tx_date,
            transactions_table.c.merchant_id,
            transactions_table.c.description,
            transactions_table.c.amount,
            transaction_category_label_expression(unknown_category).label("category"),
            transactions_table.c.transaction_kind,
        )
        .where(
            *unknown_transaction_conditions(
                unknown_category,
                statement_id=statement_id,
                excluded_ids=excluded_ids,
            )
        )
        .order_by(transactions_table.c.id)
    )
    if limit is not None:
        statement = statement.limit(limit)

    return conn.execute(statement).mappings().fetchall()


def update_unknown_transaction_category(conn: Any, tx: Mapping[str, Any], unknown_category: str) -> Any:
    """Persist an LLM category for a transaction if it is still unknown."""
    values = {
        "category": tx["category"],
        "category_id": (
            tx.get("category_id") if tx.get("category_id") is not None else resolve_category_id(conn, tx["category"])
        ),
        "needs_review": int(tx.get("needs_review", 0)),
        "category_source": tx.get("category_source", CATEGORY_SOURCE_UNKNOWN),
        "category_confidence": tx.get("category_confidence"),
        "category_rule_id": tx.get("category_rule_id"),
        "category_metadata": category_metadata_json(tx.get("category_metadata")),
        "categorized_at": tx.get("categorized_at"),
        "reviewed_at": tx.get("reviewed_at"),
    }
    return conn.execute(
        update(transactions_table)
        .where(
            transactions_table.c.id == tx["id"],
            transaction_category_label_expression(unknown_category) == unknown_category,
        )
        .values(**values)
    )
