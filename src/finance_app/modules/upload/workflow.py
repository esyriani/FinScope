"""Background workflow helpers for the upload feature."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError as SqlAlchemyIntegrityError

from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPE_SAVINGS,
    DATE_ORDER_AUTO,
    INTERAC_DIRECTION_AUTO,
    STATEMENT_IMPORT_MODE_ENRICHMENT,
    STATEMENT_IMPORT_STATUS_COMPLETED,
    STATEMENT_IMPORT_STATUS_FAILED,
    STATEMENT_IMPORT_STATUS_RUNNING,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
    TRANSACTION_KIND_EXPENSE,
    UNKNOWN_CATEGORY,
)
from finance_app.core.money import money_to_decimal
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
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
from finance_app.modules.settings.runtime import get_unknown_category
from finance_app.modules.statements.importer import parse_csv_transactions
from finance_app.modules.transactions.importer import filter_new_transactions
from finance_app.modules.upload.ai_workflow import (
    count_statement_unknown_transactions,
    queue_statement_llm_categorization,
    should_auto_queue_statement_llm,
)
from finance_app.modules.upload.messages import (
    upload_result_message,
)
from finance_app.modules.upload.repository import (
    claim_statement_import,
    update_statement_import_state,
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
INTERAC_MATCH_AMOUNT_TOLERANCE = Decimal("0.005")
INTERAC_DESCRIPTION_MARKERS: dict[str, tuple[str, ...]] = {
    "sent": ("ENVOI", "SENT E-TRANSFER"),
    "received": ("RECEPT", "RECEIVED E-TRANSFER"),
}


class StatementImportClaimError(RuntimeError):
    """Raised when a statement import attempt loses its database claim."""


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
        statement_id,
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
    amount = money_to_decimal(transfer["amount"])
    conditions = [
        transactions_table.c.ignored == 0,
        transactions_table.c.amount > amount - INTERAC_MATCH_AMOUNT_TOLERANCE,
        transactions_table.c.amount < amount + INTERAC_MATCH_AMOUNT_TOLERANCE,
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
    import_token: str,
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
            claimed = claim_statement_import(
                conn,
                statement_id,
                import_token,
                utc_timestamp(),
            )
            if not claimed:
                return "Statement import was already claimed by another attempt."

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
            updated = update_statement_import_state(
                conn,
                statement_id,
                STATEMENT_IMPORT_STATUS_COMPLETED,
                expected_statuses=(STATEMENT_IMPORT_STATUS_RUNNING,),
                expected_import_token=import_token,
                import_error=None,
                import_finished_at=utc_timestamp(),
                imported_count=inserted_count,
                skipped_count=skipped_count,
                ignored_count=ignored_count,
                llm_candidate_count=llm_candidate_count,
            )
            if not updated:
                raise StatementImportClaimError("Statement import attempt is no longer active.")
        if auto_queue_llm:
            auto_llm_job_id = queue_statement_llm_categorization(statement_id)
    except Exception as exc:
        with db_core_transaction() as conn:
            update_statement_import_state(
                conn,
                statement_id,
                STATEMENT_IMPORT_STATUS_FAILED,
                expected_statuses=(STATEMENT_IMPORT_STATUS_RUNNING,),
                expected_import_token=import_token,
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
