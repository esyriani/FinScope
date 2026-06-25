"""Background workflow helpers for the upload feature."""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError as SqlAlchemyIntegrityError

from finance_app.background.runner import (
    AI_JOB_QUEUE,
    append_background_job_log,
    is_job_cancel_requested,
    raise_if_cancel_requested,
    submit_background_job,
    update_background_job_progress,
)
from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPE_SAVINGS,
    DATE_ORDER_AUTO,
    INTERAC_DIRECTION_AUTO,
    STATEMENT_IMPORT_MODE_ENRICHMENT,
    STATEMENT_IMPORT_STATUS_COMPLETED,
    STATEMENT_IMPORT_STATUS_FAILED,
    STATEMENT_IMPORT_STATUS_QUEUED,
    STATEMENT_IMPORT_STATUS_RUNNING,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
    TRANSACTION_KIND_EXPENSE,
    UNKNOWN_CATEGORY,
)
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    statements as statements_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories import llm as llm_module
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.service import categorize_transactions
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_MANUAL,
    CATEGORY_SOURCE_UNKNOWN,
    category_metadata_json,
    utc_timestamp,
)
from finance_app.modules.categories.taxonomy import (
    set_transaction_tags,
)
from finance_app.modules.merchants.repository import (
    get_or_create_merchant_for_description,
    get_or_create_merchant_for_name,
)
from finance_app.modules.settings.runtime import confirm_ai_token_usage_enabled, get_unknown_category
from finance_app.modules.statements.importer import parse_csv_transactions
from finance_app.modules.transactions.importer import filter_new_transactions
from finance_app.modules.upload.messages import (
    ai_batch_report,
    ai_request_status_needs_log,
    automatic_categorization_message,
    format_failure_counts,
    merge_source_counts,
    upload_result_message,
)
from finance_app.modules.upload.transaction_kinds import (
    apply_transaction_kind_categories,
    classify_transaction_kind,
    date_window_end,
    date_window_start,
    default_import_mode,
    mark_linked_account_payments,
    nearest_unique_match,
    record_interac_undo_state,
    transaction_snapshot_select,
)
from finance_app.modules.upload.undo import (
    delete_statement,
    delete_statement_transactions,
    restore_interac_undo_state,
    statement_filename_row,
    statement_transaction_count,
)

INTERAC_MATCH_DATE_TOLERANCE_DAYS = 5
INTERAC_DESCRIPTION_MARKERS: dict[str, tuple[str, ...]] = {
    "sent": ("ENVOI", "SENT E-TRANSFER"),
    "received": ("RECEPT", "RECEIVED E-TRANSFER"),
}


def import_transactions(
    conn: Any,
    statement_id: int,
    account_id: int,
    statement_type: str,
    extension: str,
    raw_text: str,
    undo_state: dict[str, Any] | None = None,
    import_mode: str | None = None,
    interac_direction: str = INTERAC_DIRECTION_AUTO,
    date_order: str = DATE_ORDER_AUTO,
    categorizer: Any = None,
    tag_setter: Any = None,
) -> tuple[int, int, int]:
    """Import parsed statement transactions.

    Args:
        conn: Open SQLAlchemy Core connection.
        statement_id: Persisted statement primary key.
        account_id: Account receiving the imported statement rows.
        statement_type: Parser type for the statement.
        extension: Uploaded file extension.
        raw_text: Decoded statement contents.
        undo_state: Optional mutable state used by background-job undo.
        import_mode: Optional explicit import mode.
        interac_direction: Direction override for Interac history imports.
        date_order: Date parsing override for ambiguous statement dates.
        categorizer: Optional transaction categorization function.
        tag_setter: Optional transaction tag persistence function.

    Returns:
        A tuple of inserted, skipped, and ignored row counts.
    """
    categorizer = categorizer or categorize_transactions
    tag_setter = tag_setter or set_transaction_tags
    inserted_count = 0
    skipped_count = 0
    ignored_count = 0
    import_mode = import_mode or default_import_mode(statement_type)
    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        import_mode = STATEMENT_IMPORT_MODE_ENRICHMENT

    if extension != "csv":
        return inserted_count, skipped_count, ignored_count

    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        parse_result = parse_csv_transactions(
            raw_text,
            statement_type,
            interac_direction=interac_direction,
            date_order=date_order,
        )
    else:
        parse_result = parse_csv_transactions(
            raw_text,
            statement_type,
            date_order=date_order,
        )
    ignored_count = parse_result["ignored_rows"]
    if import_mode == STATEMENT_IMPORT_MODE_ENRICHMENT:
        if statement_type != STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
            return inserted_count, skipped_count, ignored_count + len(parse_result["transactions"])
        return enrich_interac_transactions(
            conn,
            parse_result["transactions"],
            account_id,
            undo_state=undo_state,
            ignored_count=ignored_count,
            categorizer=categorizer,
            tag_setter=tag_setter,
        )

    transactions, duplicate_count = filter_new_transactions(
        conn,
        parse_result["transactions"],
        account_id,
    )
    skipped_count += duplicate_count
    for tx in transactions:
        tx["account_id"] = account_id
        merchant = get_or_create_merchant_for_description(conn, tx["description"])
        tx["merchant_id"] = merchant["id"] if merchant else None
        tx["transaction_kind"] = classify_transaction_kind(conn, account_id, tx)

    transactions = categorizer(transactions, conn=conn, use_llm=False)
    apply_transaction_kind_categories(transactions, unknown_category=get_unknown_category(conn) or UNKNOWN_CATEGORY)

    inserted_fingerprints: set[Any] = set()
    for tx in transactions:
        if tx["fingerprint"] in inserted_fingerprints:
            skipped_count += 1
            continue

        transaction_id = insert_imported_transaction_if_new(conn, statement_id, account_id, tx)
        if transaction_id is None:
            skipped_count += 1
            continue

        tag_setter(
            conn,
            transaction_id,
            tx.get("tags", []),
            source=tx.get("category_source", CATEGORY_SOURCE_UNKNOWN),
            rule_id=tx.get("category_rule_id"),
        )
        inserted_fingerprints.add(tx["fingerprint"])
        inserted_count += 1

    mark_linked_account_payments(conn, account_id, transactions, undo_state)
    return inserted_count, skipped_count, ignored_count


def insert_imported_transaction_if_new(
    conn: Any,
    statement_id: int,
    account_id: int,
    tx: Mapping[str, Any],
) -> int | None:
    """Insert one imported transaction inside a savepoint.

    Duplicate fingerprints can still appear at insert time when another import
    committed after the prefilter ran. The savepoint keeps databases such as
    PostgreSQL from aborting the surrounding statement-import transaction.
    """
    try:
        with conn.begin_nested():
            return insert_imported_transaction(conn, statement_id, account_id, tx)
    except SqlAlchemyIntegrityError:
        return None


def insert_imported_transaction(conn: Any, statement_id: int, account_id: int, tx: Mapping[str, Any]) -> int:
    """Insert one imported transaction and return the new transaction ID."""
    values = {
        "statement_id": statement_id,
        "account_id": account_id,
        "merchant_id": tx.get("merchant_id"),
        "tx_date": tx["tx_date"],
        "description": tx["description"],
        "amount": tx["amount"],
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
        "transaction_kind": tx.get("transaction_kind", TRANSACTION_KIND_EXPENSE),
        "fingerprint": tx["fingerprint"],
    }
    result = conn.execute(insert(transactions_table).values(**values))
    return result.inserted_primary_key[0]


def enrich_interac_transactions(
    conn: Any,
    transfers: Sequence[Mapping[str, Any]],
    account_id: int,
    undo_state: dict[str, Any] | None = None,
    ignored_count: int = 0,
    categorizer: Any = None,
    tag_setter: Any = None,
) -> tuple[int, int, int]:
    """Enrich matching checking-account transactions from Interac history rows.

    Interac history rows duplicate bank movements that already exist in the
    checking statement. They update the matched ledger transaction with the real
    merchant and then re-run rule categorization for that row.
    Unmatched or ambiguous rows are not inserted as transactions.
    """
    categorizer = categorizer or categorize_transactions
    tag_setter = tag_setter or set_transaction_tags
    enriched_count = 0
    skipped_count = 0

    for transfer in transfers:
        merchant = get_or_create_merchant_for_name(conn, transfer["interac_merchant"])
        if merchant is None:
            ignored_count += 1
            continue

        match = find_interac_match(conn, account_id, transfer, merchant["id"])
        if match is None:
            ignored_count += 1
            continue
        if match == "ambiguous":
            skipped_count += 1
            continue

        record_interac_undo_state(conn, match, undo_state)
        if should_preserve_existing_category(conn, match):
            update_transaction_identity(
                conn,
                match["id"],
                merchant["id"],
                transfer["interac_merchant"],
            )
            enriched_count += 1
            continue

        enriched = categorizer(
            [
                {
                    "id": match["id"],
                    "account_id": match["account_id"],
                    "tx_date": match["tx_date"],
                    "description": transfer["interac_merchant"],
                    "amount": match["amount"],
                    "merchant_id": merchant["id"],
                }
            ],
            conn=conn,
            use_llm=False,
        )[0]
        update_enriched_transaction(
            conn,
            match["id"],
            merchant["id"],
            transfer["interac_merchant"],
            enriched,
            match["transaction_kind"],
        )
        tag_setter(
            conn,
            match["id"],
            enriched.get("tags", []),
            source=enriched.get("category_source", CATEGORY_SOURCE_UNKNOWN),
            rule_id=enriched.get("category_rule_id"),
        )
        enriched_count += 1

    return enriched_count, skipped_count, ignored_count


def update_transaction_identity(conn: Any, transaction_id: int, merchant_id: int, description: str) -> None:
    """Update only merchant identity fields for an enriched transaction."""
    conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
        .values(merchant_id=merchant_id, description=description)
    )


def update_enriched_transaction(
    conn: Any,
    transaction_id: int,
    merchant_id: int,
    description: str,
    enriched: Mapping[str, Any],
    transaction_kind: str,
) -> None:
    """Persist merchant and categorization fields for an enriched transaction."""
    values = {
        "merchant_id": merchant_id,
        "description": description,
        "category": enriched["category"],
        "category_id": (
            enriched.get("category_id")
            if enriched.get("category_id") is not None
            else resolve_category_id(conn, enriched["category"])
        ),
        "needs_review": int(enriched.get("needs_review", 0)),
        "category_source": enriched.get("category_source", CATEGORY_SOURCE_UNKNOWN),
        "category_confidence": enriched.get("category_confidence"),
        "category_rule_id": enriched.get("category_rule_id"),
        "category_metadata": category_metadata_json(enriched.get("category_metadata")),
        "categorized_at": enriched.get("categorized_at"),
        "reviewed_at": enriched.get("reviewed_at"),
        "transaction_kind": transaction_kind,
    }
    conn.execute(update(transactions_table).where(transactions_table.c.id == transaction_id).values(**values))


def should_preserve_existing_category(conn: Any, transaction: Mapping[str, Any]) -> bool:
    """Return whether Interac enrichment should leave category state unchanged."""
    category = transaction["category"]
    unknown_category = get_unknown_category(conn)
    if transaction["category_source"] == CATEGORY_SOURCE_MANUAL:
        return True
    return bool(transaction["reviewed_at"] and category and category != unknown_category)


def find_interac_match(conn: Any, account_id: int | None, transfer: Mapping[str, Any], merchant_id: int) -> Any:
    """Return the unique checking transaction matched by an Interac history row."""
    match = find_interac_match_for_account(conn, account_id, transfer, merchant_id)
    if match is not None:
        return match

    if account_id is None or account_has_transactions(conn, account_id):
        return None

    return find_interac_match_for_other_ledger_accounts(conn, account_id, transfer, merchant_id)


def account_has_transactions(conn: Any, account_id: int) -> bool:
    """Return whether the selected account already contains ledger transactions."""
    return (
        conn.execute(
            select(func.count().label("count"))
            .select_from(transactions_table)
            .where(transactions_table.c.account_id == account_id)
        ).scalar_one()
        > 0
    )


def find_interac_match_for_account(
    conn: Any,
    account_id: int | None,
    transfer: Mapping[str, Any],
    merchant_id: int,
) -> Any:
    """Return an Interac match constrained to the selected account."""
    return find_interac_match_core(
        conn,
        transfer,
        merchant_id,
        account_id=account_id,
        account_is_null=account_id is None,
    )


def find_interac_match_for_other_ledger_accounts(
    conn: Any,
    excluded_account_id: int | None,
    transfer: Mapping[str, Any],
    merchant_id: int,
) -> Any:
    """Return an Interac match in another checking or savings ledger account.

    This fallback handles users who accidentally select a separate Interac
    history account even though enrichment must update the original ledger
    account that contains the bank movement.
    """
    return find_interac_match_core(
        conn,
        transfer,
        merchant_id,
        excluded_account_id=excluded_account_id,
        require_ledger_account=True,
    )


def find_interac_match_core(
    conn: Any,
    transfer: Mapping[str, Any],
    merchant_id: int,
    account_id: int | None = None,
    account_is_null: bool = False,
    excluded_account_id: int | None = None,
    require_ledger_account: bool = False,
) -> Any:
    """Return a Core Interac match for one account scope."""
    statement = transaction_snapshot_select()
    conditions = [
        transactions_table.c.ignored == 0,
        transactions_table.c.amount > transfer["amount"] - 0.005,
        transactions_table.c.amount < transfer["amount"] + 0.005,
        transactions_table.c.tx_date >= date_window_start(transfer["tx_date"], INTERAC_MATCH_DATE_TOLERANCE_DAYS),
        transactions_table.c.tx_date <= date_window_end(transfer["tx_date"], INTERAC_MATCH_DATE_TOLERANCE_DAYS),
        interac_merchant_condition(transfer, merchant_id),
    ]

    if account_is_null:
        conditions.append(transactions_table.c.account_id.is_(None))
    elif account_id is not None:
        conditions.append(transactions_table.c.account_id == account_id)

    if excluded_account_id is not None:
        conditions.append(transactions_table.c.account_id != excluded_account_id)

    if require_ledger_account:
        statement = statement.select_from(
            transactions_table.join(
                accounts_table,
                accounts_table.c.id == transactions_table.c.account_id,
            )
        )
        conditions.append(accounts_table.c.account_type.in_((ACCOUNT_TYPE_CHECKING, ACCOUNT_TYPE_SAVINGS)))

    rows = (
        conn.execute(statement.where(*conditions).order_by(transactions_table.c.tx_date, transactions_table.c.id))
        .mappings()
        .fetchall()
    )
    return nearest_unique_match(rows, transfer["tx_date"])


def interac_merchant_condition(transfer: Mapping[str, Any], merchant_id: int) -> Any:
    """Return the Core condition that matches an Interac merchant."""
    markers = INTERAC_DESCRIPTION_MARKERS.get(str(transfer.get("interac_direction") or ""), ())
    conditions = [transactions_table.c.merchant_id == merchant_id]
    conditions.extend(func.upper(transactions_table.c.description).like(f"%{marker}%") for marker in markers)
    return or_(*conditions)


def import_statement_transactions_job(
    statement_id: int,
    account_id: int,
    statement_type: str,
    extension: str,
    raw_text: str,
    undo_state: dict[str, Any] | None = None,
    import_mode: str | None = None,
    interac_direction: str = INTERAC_DIRECTION_AUTO,
    date_order: str = DATE_ORDER_AUTO,
) -> str:
    """Import statement transactions job."""
    import_mode = import_mode or default_import_mode(statement_type)

    llm_candidate_count = 0
    auto_llm_job_id: str | None = None
    auto_queue_llm = False

    try:
        with db_core_transaction() as conn:
            update_statement_import_state(
                conn,
                statement_id,
                STATEMENT_IMPORT_STATUS_RUNNING,
                import_error=None,
                import_started_at=utc_timestamp(),
                import_finished_at=None,
            )

        with db_core_transaction() as conn:
            inserted_count, skipped_count, ignored_count = import_transactions(
                conn,
                statement_id,
                account_id,
                statement_type,
                extension,
                raw_text,
                undo_state=undo_state,
                import_mode=import_mode,
                interac_direction=interac_direction,
                date_order=date_order,
            )
            if extension == "csv" and inserted_count and statement_type != STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
                llm_candidate_count = count_statement_unknown_transactions(conn, statement_id)
                auto_queue_llm = should_auto_queue_statement_llm(conn, llm_candidate_count)
            update_statement_import_state(
                conn,
                statement_id,
                STATEMENT_IMPORT_STATUS_COMPLETED,
                import_error=None,
                import_finished_at=utc_timestamp(),
                imported_count=inserted_count,
                skipped_count=skipped_count,
                ignored_count=ignored_count,
                llm_candidate_count=llm_candidate_count,
            )
        if auto_queue_llm:
            auto_llm_job_id = queue_statement_llm_categorization(statement_id)
    except Exception as exc:
        with db_core_transaction() as conn:
            update_statement_import_state(
                conn,
                statement_id,
                STATEMENT_IMPORT_STATUS_FAILED,
                import_error=f"{type(exc).__name__}: {exc}",
                import_finished_at=utc_timestamp(),
            )
        raise

    return upload_result_message(
        statement_type,
        extension,
        inserted_count,
        skipped_count,
        ignored_count,
        llm_candidate_count=llm_candidate_count,
        auto_llm_job_id=auto_llm_job_id,
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


def reset_statement_import_state(
    conn: Any,
    statement_id: int,
    status: str = STATEMENT_IMPORT_STATUS_QUEUED,
) -> None:
    """Reset persisted import metadata before queueing a statement import."""
    update_statement_import_state(
        conn,
        statement_id,
        status,
        import_error=None,
        import_started_at=None,
        import_finished_at=None,
        imported_count=0,
        skipped_count=0,
        ignored_count=0,
        llm_candidate_count=0,
    )


def update_statement_import_state(conn: Any, statement_id: int, status: str, **fields: Any) -> None:
    """Persist import status, timestamps, counters, and errors for a statement."""
    allowed_fields = {
        "import_error",
        "import_started_at",
        "import_finished_at",
        "imported_count",
        "skipped_count",
        "ignored_count",
        "llm_candidate_count",
    }
    for field, value in fields.items():
        if field not in allowed_fields:
            raise ValueError(f"Unsupported statement import field: {field}")

    conn.execute(
        update(statements_table).where(statements_table.c.id == statement_id).values(import_status=status, **fields)
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
        (transactions_table.c.category.is_(None) | (transactions_table.c.category == unknown_category)),
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
    transaction_categorizer = transaction_categorizer or categorize_transactions
    llm_module.clear_llm_request_status()
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn)
        transactions = [
            {
                "id": row["id"],
                "account_id": row["account_id"],
                "tx_date": row["tx_date"],
                "merchant_id": row["merchant_id"],
                "description": row["description"],
                "amount": row["amount"],
                "category": row["category"] or unknown_category,
                "transaction_kind": row["transaction_kind"],
            }
            for row in rows
        ]
        categorized = transaction_categorizer(transactions, conn=conn, use_llm=True)
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
            transactions_table.c.category,
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
            (transactions_table.c.category.is_(None) | (transactions_table.c.category == unknown_category)),
        )
        .values(**values)
    )


def undo_statement_upload_job(statement_id: int, undo_state: Mapping[str, Any] | None = None) -> str:
    """Undo statement upload job."""
    with db_core_transaction() as conn:
        statement = statement_filename_row(conn, statement_id)

        if statement is None:
            return "Statement was already removed."

        transaction_count = statement_transaction_count(conn, statement_id)

        delete_statement_transactions(conn, statement_id)
        delete_statement(conn, statement_id)
        restored_count = restore_interac_undo_state(conn, undo_state)

    message = (
        f"Removed statement {statement['filename']} "
        f"and {transaction_count} transaction{'s' if transaction_count != 1 else ''}."
    )
    if restored_count:
        message += f" Restored {restored_count} enriched transaction{'s' if restored_count != 1 else ''}."
    return message
