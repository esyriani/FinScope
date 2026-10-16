"""Canonical transaction backup CSV export helpers.

This module owns the transaction backup file contract used by the Transactions
page. It reads transaction rows and transaction-owned relationship details with
SQLAlchemy Core; callers own request handling and database transaction scope.
"""

import csv
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from finance_app.core.csv_export import csv_export_value
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import merchants as merchants_table
from finance_app.database.tables import reimbursement_allocations as reimbursement_allocations_table
from finance_app.database.tables import (
    reimbursement_expense_completions as reimbursement_expense_completions_table,
)
from finance_app.database.tables import tags as tags_table
from finance_app.database.tables import transaction_tags as transaction_tags_table
from finance_app.database.tables import transactions as transactions_table

TRANSACTION_BACKUP_COLUMNS = (
    "account_name",
    "account_type",
    "paid_from_account_name",
    "paid_from_account_type",
    "merchant_name",
    "tx_date",
    "description",
    "amount",
    "category",
    "needs_review",
    "category_source",
    "category_confidence",
    "categorized_at",
    "reviewed_at",
    "ignored",
    "transaction_kind",
    "tags",
    "fingerprint",
    "reimbursement_expense_completed",
    "reimbursement_allocations",
)
QUERY_CHUNK_SIZE = 900


def export_transactions_backup_csv(conn: Any) -> str:
    """Return a canonical CSV backup of every transaction row.

    The export includes portable ledger state and relationship labels for
    accounts, paid-from accounts, merchants, categories, tags, and reimbursement
    links. Audit explanations, tag assignment history, and database IDs are not
    part of the restore contract. Every cell is sanitized through
    ``csv_export_value`` before being written so spreadsheet applications do not
    execute user-controlled values as formulas.
    """
    rows = export_transaction_rows(conn)
    transaction_ids = [int(row["transaction_id"]) for row in rows]
    tags_by_transaction = transaction_tag_export_names(conn, transaction_ids)
    reimbursement_details = reimbursement_export_details(conn, transaction_ids)

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=TRANSACTION_BACKUP_COLUMNS)
    writer.writeheader()
    for row in rows:
        transaction_id = int(row["transaction_id"])
        tag_names = tags_by_transaction.get(transaction_id, ())
        export_row = {
            **row,
            "tags": json_list(tag_names),
            "reimbursement_expense_completed": (
                1 if transaction_id in reimbursement_details.completed_expense_transaction_ids else 0
            ),
            "reimbursement_allocations": json_list(reimbursement_details.as_reimbursement.get(transaction_id, ())),
        }
        writer.writerow({column: escape_backup_cell(export_row.get(column)) for column in TRANSACTION_BACKUP_COLUMNS})
    return output.getvalue()


def escape_backup_cell(value: object) -> object:
    """Escape literal apostrophes before formula neutralization so the backup is reversible."""
    text = format_backup_cell(value)
    return csv_export_value(f"'{text}" if text.startswith("'") else text)


def export_transaction_rows(conn: Any) -> list[dict[str, Any]]:
    """Return all transaction rows with import-friendly relationship labels."""
    paid_from_accounts = accounts_table.alias("paid_from_accounts")
    category_label = func.coalesce(categories_table.c.name, transactions_table.c.category)
    rows = (
        conn.execute(
            select(
                transactions_table.c.id.label("transaction_id"),
                accounts_table.c.name.label("account_name"),
                accounts_table.c.account_type.label("account_type"),
                paid_from_accounts.c.name.label("paid_from_account_name"),
                paid_from_accounts.c.account_type.label("paid_from_account_type"),
                merchants_table.c.merchant_key.label("merchant_name"),
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                category_label.label("category"),
                transactions_table.c.needs_review,
                transactions_table.c.category_source,
                transactions_table.c.category_confidence,
                transactions_table.c.categorized_at,
                transactions_table.c.reviewed_at,
                transactions_table.c.ignored,
                transactions_table.c.transaction_kind,
                transactions_table.c.fingerprint,
            )
            .select_from(
                transactions_table.outerjoin(
                    accounts_table,
                    accounts_table.c.id == transactions_table.c.account_id,
                )
                .outerjoin(
                    paid_from_accounts,
                    paid_from_accounts.c.id == accounts_table.c.paid_from_account_id,
                )
                .outerjoin(
                    merchants_table,
                    merchants_table.c.id == transactions_table.c.merchant_id,
                )
                .outerjoin(
                    categories_table,
                    categories_table.c.id == transactions_table.c.category_id,
                )
            )
            .order_by(transactions_table.c.id)
        )
        .mappings()
        .fetchall()
    )
    return [dict(row) for row in rows]


def transaction_tag_export_names(conn: Any, transaction_ids: Sequence[int]) -> dict[int, list[str]]:
    """Return portable tag names without assignment history, keyed by transaction id."""
    result: dict[int, list[str]] = {transaction_id: [] for transaction_id in transaction_ids}
    for chunk in chunked(transaction_ids):
        rows = (
            conn.execute(
                select(
                    transaction_tags_table.c.transaction_id,
                    tags_table.c.name,
                )
                .join(tags_table, tags_table.c.id == transaction_tags_table.c.tag_id)
                .where(transaction_tags_table.c.transaction_id.in_(chunk))
                .order_by(
                    transaction_tags_table.c.transaction_id,
                    func.lower(tags_table.c.name),
                    tags_table.c.name,
                )
            )
            .mappings()
            .fetchall()
        )
        for row in rows:
            result.setdefault(int(row["transaction_id"]), []).append(row["name"])
    return result


class ReimbursementExportDetails:
    """Grouped reimbursement details for transaction backup rows."""

    def __init__(
        self,
        *,
        completed_expense_transaction_ids: set[int],
        as_reimbursement: dict[int, list[dict[str, Any]]],
    ) -> None:
        """Store reimbursement export details keyed by transaction role."""
        self.completed_expense_transaction_ids = completed_expense_transaction_ids
        self.as_reimbursement = as_reimbursement


def reimbursement_export_details(conn: Any, transaction_ids: Sequence[int]) -> ReimbursementExportDetails:
    """Return reimbursement allocation and completion details for transactions."""
    completed = completed_reimbursement_expense_transaction_ids(conn, transaction_ids)
    return ReimbursementExportDetails(
        completed_expense_transaction_ids=completed,
        as_reimbursement=reimbursement_allocations_as_reimbursement(conn, transaction_ids),
    )


def completed_reimbursement_expense_transaction_ids(conn: Any, transaction_ids: Sequence[int]) -> set[int]:
    """Return transaction ids with reimbursement expense completion markers."""
    completed: set[int] = set()
    for chunk in chunked(transaction_ids):
        completed.update(
            int(value)
            for value in conn.execute(
                select(reimbursement_expense_completions_table.c.expense_transaction_id).where(
                    reimbursement_expense_completions_table.c.expense_transaction_id.in_(chunk)
                )
            ).scalars()
        )
    return completed


def reimbursement_allocations_as_reimbursement(
    conn: Any,
    transaction_ids: Sequence[int],
) -> dict[int, list[dict[str, Any]]]:
    """Return allocations where the transaction is the reimbursement credit."""
    result: dict[int, list[dict[str, Any]]] = {transaction_id: [] for transaction_id in transaction_ids}
    expense_transactions = transactions_table.alias("expense_transactions")
    for chunk in chunked(transaction_ids):
        rows = (
            conn.execute(
                select(
                    reimbursement_allocations_table.c.reimbursement_transaction_id,
                    expense_transactions.c.fingerprint.label("expense_fingerprint"),
                    reimbursement_allocations_table.c.amount,
                )
                .join(
                    expense_transactions,
                    expense_transactions.c.id == reimbursement_allocations_table.c.expense_transaction_id,
                )
                .where(reimbursement_allocations_table.c.reimbursement_transaction_id.in_(chunk))
                .order_by(
                    reimbursement_allocations_table.c.reimbursement_transaction_id,
                    reimbursement_allocations_table.c.id,
                )
            )
            .mappings()
            .fetchall()
        )
        for row in rows:
            result.setdefault(int(row["reimbursement_transaction_id"]), []).append(
                reimbursement_allocation_payload(row)
            )
    return result


def reimbursement_allocation_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe allocation payload for one transaction row."""
    return {
        "expense_fingerprint": row["expense_fingerprint"],
        "amount": format_backup_cell(row["amount"]),
    }


def json_list(values: Iterable[object]) -> str:
    """Return a compact ASCII JSON list for a CSV cell."""
    items = list(values)
    if not items:
        return ""
    return json.dumps(items, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def format_backup_cell(value: object) -> str:
    """Return a stable text representation for a transaction backup CSV cell."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def chunked(values: Sequence[int], size: int = QUERY_CHUNK_SIZE) -> Iterable[Sequence[int]]:
    """Yield stable chunks for SQL ``IN`` predicates."""
    for index in range(0, len(values), size):
        chunk = values[index : index + size]
        if chunk:
            yield chunk
