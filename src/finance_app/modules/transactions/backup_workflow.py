"""Background transaction backup restoration and queue orchestration.

Confirmation claims a user-owned upload once and submits the existing main
background runner only when rows remain to import. Workers recheck duplicates
and restore references, ledger rows, tags and reimbursements in one Core
transaction. Progress is published outside that write transaction to avoid
competing SQLite history writes.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from finance_app.background.runner import (
    BackgroundJobSubmissionError,
    append_background_job_log,
    raise_if_cancel_requested,
    submit_background_job,
    update_background_job_progress,
)
from finance_app.core.i18n import current_language, gettext, translate
from finance_app.core.money import money_to_decimal
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import normalize_name_key
from finance_app.modules.categories.taxonomy import transaction_tag_names_for_persistence
from finance_app.modules.reimbursements import repository as reimbursement_repository
from finance_app.modules.reimbursements.service import validate_expense_transaction, validate_reimbursement_transaction
from finance_app.modules.transactions import backup_repository as repository
from finance_app.modules.transactions.backup_import import (
    DUPLICATE_IMPORT_ACTION_IMPORT_ALL,
    DUPLICATE_IMPORT_ACTION_SKIP,
    TransactionBackupImportPreview,
    existing_transactions_by_fingerprint,
    normalize_duplicate_import_action,
    preview_transactions_backup_import,
)
from finance_app.modules.transactions.backup_payloads import transaction_backup_import_payload_store

INVALID_BACKUP_MESSAGE = "The backup contains invalid rows or conflicting details. Fix the backup and upload it again."
CANCEL_IMPORT_MESSAGE = "Transaction backup import cancelled. No changes were saved."


@dataclass(frozen=True)
class TransactionBackupImportResult:
    """Counts published after a backup restore commits successfully."""

    imported: int
    skipped: int
    allocations: int


def queue_transactions_backup_import(reference: object, user_id: str, duplicate_action: object) -> dict[str, Any]:
    """Claim a valid preview once, skip empty restores, and retain rejected submissions."""
    action = normalize_duplicate_import_action(duplicate_action)
    if not action:
        return {"ok": False, "message": "Choose how duplicate transactions should be handled."}
    store = transaction_backup_import_payload_store()
    payload = store.claim(reference, user_id)
    if payload is None:
        return {"ok": False, "message": "Transaction backup import preview expired. Upload the backup again."}
    try:
        with db_core_transaction() as conn:
            preview = preview_transactions_backup_import(conn, payload.csv_text)
            validate_backup_preview(preview)
    except ValueError:
        return {"ok": False, "message": INVALID_BACKUP_MESSAGE}
    if action == DUPLICATE_IMPORT_ACTION_SKIP and not preview.importable_without_duplicates_count:
        return {"ok": True, "message": "All transactions were skipped as duplicates. Nothing was imported."}
    try:
        job_id = submit_background_job(
            gettext("Import transaction backup"),
            import_transactions_backup_job,
            payload.csv_text,
            action,
            language=current_language(),
        )
    except BackgroundJobSubmissionError:
        store.release(str(reference), payload)
        return {
            "ok": False,
            "retry_reference": reference,
            "message": "Transaction backup import could not be queued. Try again.",
        }
    return {
        "ok": True,
        "job_id": job_id,
        "message": "Transaction backup import queued. Processing item: {job_id}",
        "params": {"job_id": job_id[:8]},
    }


def pending_transactions_backup_preview(reference: object, user_id: str) -> TransactionBackupImportPreview | None:
    """Rebuild a user-owned pending preview for the Transactions GET page."""
    payload = transaction_backup_import_payload_store().get(reference, user_id)
    if payload is None:
        return None
    with db_core_transaction() as conn:
        return preview_transactions_backup_import(conn, payload.csv_text)


def validate_backup_preview(preview: TransactionBackupImportPreview) -> None:
    """Reject partial or inconsistent backups before any changes are written."""
    if not preview.valid_row_count or preview.row_errors:
        raise ValueError(INVALID_BACKUP_MESSAGE)
    for row in preview.transactions:
        if row["account_name"] and normalize_name_key(row["account_name"]) == normalize_name_key(
            row["paid_from_account_name"]
        ):
            raise ValueError(INVALID_BACKUP_MESSAGE)


def import_transactions_backup_job(csv_text: str, duplicate_action: str, *, language: str = "en") -> str:
    """Restore a backup atomically and publish counts through persisted job history."""
    action = normalize_duplicate_import_action(duplicate_action)
    if not action:
        raise ValueError("Choose how duplicate transactions should be handled.")
    raise_if_cancel_requested(CANCEL_IMPORT_MESSAGE)
    append_background_job_log("Starting transaction backup import.")
    update_background_job_progress(current=0, message="Restoring transaction backup...")
    try:
        with db_core_transaction() as conn:
            preview = preview_transactions_backup_import(conn, csv_text)
            validate_backup_preview(preview)
            result = restore_transactions_backup(conn, preview, action)
            raise_if_cancel_requested(CANCEL_IMPORT_MESSAGE)
    except SQLAlchemyError:
        # SQL exceptions can include descriptions, amounts, and uploaded values.
        raise ValueError("Transaction backup import failed. No changes were saved.") from None
    params = {"imported": result.imported, "skipped": result.skipped, "allocations": result.allocations}
    summary = "Transaction backup import completed: {imported} imported, {skipped} duplicates skipped, {allocations} reimbursement links restored."
    append_background_job_log(summary, params=params)
    update_background_job_progress(
        current=preview.valid_row_count, total=preview.valid_row_count, message=summary, params=params
    )
    return translate(summary, language, **params)


def restore_transactions_backup(
    conn: Connection, preview: TransactionBackupImportPreview, action: str
) -> TransactionBackupImportResult:
    """Restore validated rows with the caller owning commit or rollback."""
    account_ids = repository.restore_backup_accounts(conn, preview.accounts)
    duplicates = {
        item.line_number: item.existing_transaction_id
        for item in preview.duplicates
        if item.existing_transaction_id is not None
    }
    fingerprint_ids: dict[str, int] = {}
    target_ids: dict[int, int] = {}
    new_ids: set[int] = set()
    natural_ids: dict[tuple[Any, ...], int] = {}
    skipped = 0
    for row in preview.transactions:
        raise_if_cancel_requested(CANCEL_IMPORT_MESSAGE)
        natural_key = (
            account_ids.get(normalize_name_key(row["account_name"])),
            row["tx_date"],
            row["description"],
            row["amount"],
        )
        duplicate_id = (
            duplicates.get(row["line_number"])
            or fingerprint_ids.get(row["fingerprint"])
            or natural_ids.get(natural_key)
        )
        if action == DUPLICATE_IMPORT_ACTION_SKIP and duplicate_id is not None:
            transaction_id = duplicate_id
            skipped += 1
        else:
            transaction_id, inserted = repository.restore_backup_transaction(
                conn, row, account_ids, allow_duplicate=action == DUPLICATE_IMPORT_ACTION_IMPORT_ALL
            )
            if inserted:
                new_ids.add(transaction_id)
                repository.restore_backup_tags(conn, transaction_id, row["tags"])
                if row["tags"]:
                    allowed_tags = transaction_tag_names_for_persistence(conn, transaction_id, row["tags"])
                    if {normalize_name_key(name) for name in allowed_tags} != {
                        normalize_name_key(name) for name in row["tags"]
                    }:
                        raise ValueError(
                            "The backup contains tag assignments that are not allowed for these transactions."
                        )
            else:
                skipped += 1
        fingerprint_ids.setdefault(row["fingerprint"], transaction_id)
        natural_ids.setdefault(natural_key, transaction_id)
        target_ids[row["line_number"]] = transaction_id
    allocations = restore_backup_relationships(conn, preview, fingerprint_ids, target_ids, new_ids)
    return TransactionBackupImportResult(imported=len(new_ids), skipped=skipped, allocations=allocations)


def restore_backup_relationships(
    conn: Connection,
    preview: TransactionBackupImportPreview,
    fingerprint_ids: Mapping[str, int],
    target_ids: Mapping[int, int],
    new_ids: set[int],
) -> int:
    """Restore credit-owned reimbursement links once and preserve existing skipped rows."""
    links: dict[tuple[int, int], Mapping[str, Any]] = {}
    for row in preview.transactions:
        own_id = target_ids[row["line_number"]]
        for allocation in row["reimbursement_allocations"]:
            fingerprint = allocation["expense_fingerprint"]
            expense_id = fingerprint_ids.get(fingerprint)
            if expense_id is None:
                expense = existing_transactions_by_fingerprint(conn, [fingerprint]).get(fingerprint)
                expense_id = int(expense["id"]) if expense else None
            if expense_id is None:
                raise ValueError("A reimbursement link points to a transaction missing from the backup.")
            pair = (own_id, expense_id)
            if not new_ids.intersection(pair):
                continue
            if pair in links and links[pair].get("amount") != allocation.get("amount"):
                raise ValueError("The backup contains conflicting reimbursement links.")
            links[pair] = allocation
    for (reimbursement_id, expense_id), allocation in links.items():
        raise_if_cancel_requested(CANCEL_IMPORT_MESSAGE)
        restore_validated_allocation(conn, reimbursement_id, expense_id, allocation)
    for row in preview.transactions:
        expense_id = target_ids[row["line_number"]]
        if row["reimbursement_expense_completed"] and expense_id in new_ids:
            subject = reimbursement_repository.get_transaction_allocation_subject(conn, expense_id)
            assert subject is not None
            validate_expense_transaction({**subject, "ignored": 0})
            repository.restore_backup_expense_completion(conn, expense_id)
    return len(links)


def restore_validated_allocation(
    conn: Connection, reimbursement_id: int, expense_id: int, allocation: Mapping[str, Any]
) -> None:
    """Validate archival link roles and totals while preserving ignored-row relationships."""
    if reimbursement_id == expense_id:
        raise ValueError("A reimbursement cannot be matched to itself.")
    amount = allocation.get("amount")
    if amount is None or amount <= 0:
        raise ValueError("The backup contains an invalid reimbursement amount.")
    subjects = reimbursement_repository.lock_transaction_allocation_subjects(conn, (reimbursement_id, expense_id))
    reimbursement = subjects[reimbursement_id]
    expense = subjects[expense_id]
    # Ignoring a transaction suspends analytics; it does not delete its saved links.
    validate_reimbursement_transaction({**reimbursement, "ignored": 0})
    validate_expense_transaction({**expense, "ignored": 0})
    credit_total, expense_total = repository.backup_allocation_totals(conn, reimbursement_id, expense_id)
    if money_to_decimal(credit_total) + amount > abs(money_to_decimal(reimbursement["amount"])) or money_to_decimal(
        expense_total
    ) + amount > money_to_decimal(expense["amount"]):
        raise ValueError("The backup reimbursement links exceed the transaction amounts.")
    repository.restore_backup_allocation(conn, reimbursement_id, expense_id, allocation)
