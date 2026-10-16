"""Transaction backup CSV parsing and read-only import previews.

This module owns the import side of the transaction backup CSV contract. It
validates rows exported by ``transactions.exporter`` and compares their
account, merchant, category, and tag references with the current database using
SQLAlchemy Core. Preview helpers do not mutate data; callers own transaction
scope and later workflow decisions.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.engine import Connection as CoreConnection

from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPES,
    CATEGORY_SOURCE_UNKNOWN,
    CATEGORY_SOURCES,
    TRANSACTION_KINDS,
)
from finance_app.core.csv_export import CSV_FORMULA_PREFIXES
from finance_app.core.i18n import gettext
from finance_app.core.money import money_to_decimal, quantize_money
from finance_app.core.taxonomy import clean_label
from finance_app.database.dates import coerce_date
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import merchants as merchants_table
from finance_app.database.tables import normalize_name_key
from finance_app.database.tables import tags as tags_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.accounts.repository import normalize_account_name
from finance_app.modules.categories.repository import clean_category_name
from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.transactions.exporter import TRANSACTION_BACKUP_COLUMNS

PREVIEW_ACTION_CREATE = "create"
PREVIEW_ACTION_EXISTING = "existing"
PREVIEW_ACTION_UPDATE = "update"
DUPLICATE_MATCH_DETAILS = "details"
DUPLICATE_MATCH_FINGERPRINT = "fingerprint"
DUPLICATE_MATCH_BACKUP = "backup"
DUPLICATE_IMPORT_ACTION_IMPORT_ALL = "import_all"
DUPLICATE_IMPORT_ACTION_SKIP = "skip_duplicates"
DUPLICATE_IMPORT_ACTIONS = (
    DUPLICATE_IMPORT_ACTION_IMPORT_ALL,
    DUPLICATE_IMPORT_ACTION_SKIP,
)
QUERY_CHUNK_SIZE = 900


@dataclass(frozen=True)
class BackupReferencePreview:
    """Describe how one imported label would resolve against existing rows."""

    name: str
    action: str
    existing_id: int | None = None


@dataclass(frozen=True)
class BackupAccountPreview:
    """Describe how one imported account would resolve or be updated."""

    name: str
    account_type: str
    paid_from_account_name: str
    action: str
    existing_id: int | None = None
    changes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackupDuplicatePreview:
    """Describe an imported row matching an existing transaction or earlier backup row."""

    line_number: int
    match_type: str
    existing_transaction_id: int | None
    tx_date: date
    description: str
    amount: Decimal
    account_name: str
    existing_account_name: str
    fingerprint: str
    duplicate_of_line_number: int | None = None

    @property
    def reason_label(self) -> str:
        """Return a user-facing duplicate reason label."""
        if self.match_type == DUPLICATE_MATCH_FINGERPRINT:
            return "Previously imported transaction"
        if self.match_type == DUPLICATE_MATCH_BACKUP:
            return "Repeated transaction in this backup"
        return "Same date, account, description, and amount"


@dataclass(frozen=True)
class TransactionBackupCsvParseResult:
    """Rows and row-level validation issues parsed from a backup CSV."""

    total_rows: int
    transactions: tuple[dict[str, Any], ...]
    row_errors: tuple[str, ...]


@dataclass(frozen=True)
class TransactionBackupImportPreview:
    """Read-only transaction backup import plan for later confirmation."""

    total_rows: int
    valid_row_count: int
    transactions: tuple[dict[str, Any], ...]
    row_errors: tuple[str, ...]
    accounts: tuple[BackupAccountPreview, ...]
    categories: tuple[BackupReferencePreview, ...]
    tags: tuple[BackupReferencePreview, ...]
    merchants: tuple[BackupReferencePreview, ...]
    duplicates: tuple[BackupDuplicatePreview, ...]

    @property
    def duplicate_count(self) -> int:
        """Return the number of imported rows that appear to already exist."""
        return len(self.duplicates)

    @property
    def importable_without_duplicates_count(self) -> int:
        """Return rows that would remain when duplicate rows are skipped."""
        return max(0, self.valid_row_count - self.duplicate_count)


class BackupCsvRowError(ValueError):
    """Raised when a single transaction backup CSV row cannot be imported."""


def preview_transactions_backup_import(conn: CoreConnection, csv_text: str) -> TransactionBackupImportPreview:
    """Return a read-only import preview for a transaction backup CSV.

    Args:
        conn: Open SQLAlchemy Core connection. The caller owns the transaction.
        csv_text: Raw CSV text from a FinScope transaction backup export.

    Returns:
        Parsed transaction rows plus reference plans for accounts, categories,
        tags, and merchants. Invalid rows are reported through ``row_errors``.

    Raises:
        TypeError: If ``conn`` is not a SQLAlchemy Core connection.
        ValueError: If the CSV is empty or does not match the backup schema.
    """
    if not isinstance(conn, CoreConnection):
        raise TypeError("Transaction backup import preview requires a SQLAlchemy Core connection.")

    parsed = parse_transactions_backup_csv(csv_text)
    existing_accounts = existing_accounts_by_key(conn)
    accounts, account_errors = build_account_previews(conn, parsed.transactions, existing_accounts)
    return TransactionBackupImportPreview(
        total_rows=parsed.total_rows,
        valid_row_count=len(parsed.transactions),
        transactions=parsed.transactions,
        row_errors=(*parsed.row_errors, *account_errors),
        accounts=accounts,
        categories=build_named_reference_previews(
            conn,
            categories_table,
            category_names_from_transactions(parsed.transactions),
        ),
        tags=build_named_reference_previews(conn, tags_table, tag_names_from_transactions(parsed.transactions)),
        merchants=build_merchant_previews(conn, merchant_names_from_transactions(parsed.transactions)),
        duplicates=build_duplicate_previews(conn, parsed.transactions, existing_accounts),
    )


def normalize_duplicate_import_action(value: object) -> str:
    """Return a supported duplicate-handling choice for backup import."""
    text = str(value or "").strip()
    return text if text in DUPLICATE_IMPORT_ACTIONS else ""


def parse_transactions_backup_csv(csv_text: str) -> TransactionBackupCsvParseResult:
    """Parse backup CSV text into typed transaction rows and row errors."""
    normalized_csv = str(csv_text or "").lstrip("\ufeff")
    if not normalized_csv.strip():
        raise ValueError(gettext("The transaction backup CSV is empty."))

    reader = csv.DictReader(io.StringIO(normalized_csv, newline=""), strict=True)
    try:
        headings = reader.fieldnames
    except csv.Error:
        raise ValueError(gettext("The backup CSV could not be read. Check the file format.")) from None
    if not headings:
        raise ValueError(gettext("The transaction backup CSV is missing a header row."))
    if len(headings) != len(set(headings)):
        raise ValueError(gettext("The backup CSV contains repeated column headings."))

    missing_columns = [column for column in TRANSACTION_BACKUP_COLUMNS if column not in headings]
    if missing_columns:
        joined_columns = ", ".join(missing_columns)
        raise ValueError(gettext("The transaction backup CSV is missing columns: {columns}.", columns=joined_columns))

    total_rows = 0
    transactions: list[dict[str, Any]] = []
    row_errors: list[str] = []
    try:
        for raw_row in reader:
            if not backup_row_has_values(raw_row):
                continue
            total_rows += 1
            try:
                if None in raw_row or any(raw_row.get(column) is None for column in TRANSACTION_BACKUP_COLUMNS):
                    row_error(reader.line_num, "The number of values does not match the column headings.")
                transactions.append(parse_transaction_backup_row(raw_row, reader.line_num))
            except BackupCsvRowError as exc:
                row_errors.append(str(exc))
    except csv.Error:
        raise ValueError(gettext("The backup CSV could not be read. Check the file format.")) from None

    if total_rows == 0:
        raise ValueError(gettext("The transaction backup CSV does not contain transaction rows."))

    return TransactionBackupCsvParseResult(
        total_rows=total_rows,
        transactions=tuple(transactions),
        row_errors=tuple(row_errors),
    )


def parse_transaction_backup_row(row: Mapping[str, Any], line_number: int) -> dict[str, Any]:
    """Parse one CSV row into typed backup import fields."""
    account_name = optional_label(row, "account_name")
    account_type = parse_account_type(row, line_number)
    paid_from_account_name = optional_label(row, "paid_from_account_name")
    if paid_from_account_name and not account_name:
        row_error(line_number, "A funding account requires an account name.")

    return {
        "line_number": line_number,
        "account_name": account_name,
        "account_type": account_type,
        "paid_from_account_name": paid_from_account_name,
        "paid_from_account_type": parse_enum_field(
            row, "paid_from_account_type", ACCOUNT_TYPES, None if paid_from_account_name else "", line_number
        ),
        "merchant_name": optional_text(row, "merchant_name").strip(),
        "tx_date": parse_required_date(row, "tx_date", line_number),
        "description": required_text(row, "description", line_number),
        "amount": parse_required_money(row, "amount", line_number),
        "category": optional_category_label(row, "category"),
        "needs_review": parse_bool_int(row, "needs_review", line_number),
        "category_source": parse_enum_field(
            row,
            "category_source",
            CATEGORY_SOURCES,
            CATEGORY_SOURCE_UNKNOWN,
            line_number,
        ),
        "category_confidence": parse_optional_probability(row, "category_confidence", line_number),
        "categorized_at": parse_optional_timestamp(row, "categorized_at", line_number),
        "reviewed_at": parse_optional_timestamp(row, "reviewed_at", line_number),
        "ignored": parse_bool_int(row, "ignored", line_number),
        "transaction_kind": parse_enum_field(row, "transaction_kind", TRANSACTION_KINDS, None, line_number),
        "tags": parse_tag_names(row, line_number),
        "fingerprint": required_text(row, "fingerprint", line_number).strip(),
        "reimbursement_expense_completed": parse_bool_int(row, "reimbursement_expense_completed", line_number),
        "reimbursement_allocations": parse_reimbursement_allocations(row, line_number),
    }


def build_account_previews(
    conn: CoreConnection,
    transactions: Sequence[Mapping[str, Any]],
    existing_accounts: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[BackupAccountPreview, ...], tuple[str, ...]]:
    """Return account reference previews and file-level account conflicts."""
    desired_accounts: dict[str, dict[str, str]] = {}
    errors: list[str] = []

    for transaction in transactions:
        account_name = str(transaction["account_name"])
        if account_name:
            remember_desired_account(
                desired_accounts,
                errors,
                account_name,
                str(transaction["account_type"]),
                str(transaction["paid_from_account_name"]),
            )

    for transaction in transactions:
        paid_from_name = str(transaction["paid_from_account_name"])
        if paid_from_name:
            paid_from_key = normalize_name_key(paid_from_name)
            planned_funding = desired_accounts.get(paid_from_key)
            existing_funding = existing_accounts.get(paid_from_key)
            funding_name = str(existing_funding["paid_from_account_name"] or "") if existing_funding else ""
            if planned_funding:
                funding_name = planned_funding["paid_from_account_name"]
            remember_desired_account(
                desired_accounts,
                errors,
                paid_from_name,
                str(transaction["paid_from_account_type"]),
                funding_name,
            )

    previews: list[BackupAccountPreview] = []
    for account_key, desired in desired_accounts.items():
        existing = existing_accounts.get(account_key)
        existing_id = int(existing["id"]) if existing is not None else None
        changes: list[str] = []
        if existing is not None:
            if existing["account_type"] != desired["account_type"]:
                changes.append("account_type")
            if normalize_name_key(existing["paid_from_account_name"]) != normalize_name_key(
                desired["paid_from_account_name"]
            ):
                changes.append("paid_from_account")

        action = (
            PREVIEW_ACTION_CREATE if existing is None else PREVIEW_ACTION_UPDATE if changes else PREVIEW_ACTION_EXISTING
        )
        previews.append(
            BackupAccountPreview(
                name=desired["name"],
                account_type=desired["account_type"],
                paid_from_account_name=desired["paid_from_account_name"],
                action=action,
                existing_id=existing_id,
                changes=tuple(changes),
            )
        )

    return tuple(previews), tuple(errors)


def build_duplicate_previews(
    conn: CoreConnection,
    transactions: Sequence[Mapping[str, Any]],
    existing_accounts: Mapping[str, Mapping[str, Any]],
) -> tuple[BackupDuplicatePreview, ...]:
    """Return imported rows that may already exist in the database."""
    existing_by_fingerprint = existing_transactions_by_fingerprint(
        conn,
        [str(transaction["fingerprint"]) for transaction in transactions],
    )
    existing_by_details = existing_transactions_by_details(conn, transactions, existing_accounts)
    duplicates: list[BackupDuplicatePreview] = []
    seen_fingerprints: dict[str, int] = {}
    seen_details: dict[tuple[Any, ...], int] = {}

    for transaction in transactions:
        fingerprint = str(transaction["fingerprint"])
        line_number = int(transaction["line_number"])
        file_key = (
            normalize_name_key(transaction["account_name"]),
            transaction["tx_date"],
            transaction["description"],
            transaction["amount"],
        )
        repeated_line = seen_fingerprints.get(fingerprint) or seen_details.get(file_key)
        seen_fingerprints.setdefault(fingerprint, line_number)
        seen_details.setdefault(file_key, line_number)
        existing = existing_by_fingerprint.get(fingerprint)
        match_type = DUPLICATE_MATCH_FINGERPRINT
        if existing is None:
            match_type = DUPLICATE_MATCH_DETAILS
            existing = existing_by_details.get(duplicate_details_key(transaction, existing_accounts))
        if existing is None and repeated_line is None:
            continue
        if existing is None:
            match_type = DUPLICATE_MATCH_BACKUP

        duplicates.append(
            BackupDuplicatePreview(
                line_number=line_number,
                match_type=match_type,
                existing_transaction_id=int(existing["id"]) if existing is not None else None,
                tx_date=transaction["tx_date"],
                description=str(transaction["description"]),
                amount=transaction["amount"],
                account_name=str(transaction["account_name"]),
                existing_account_name=str(existing["account_name"] or "") if existing is not None else "",
                fingerprint=fingerprint,
                duplicate_of_line_number=repeated_line if existing is None else None,
            )
        )

    return tuple(duplicates)


def existing_transactions_by_fingerprint(
    conn: CoreConnection,
    fingerprints: Sequence[str],
) -> dict[str, Mapping[str, Any]]:
    """Return existing transactions keyed by fingerprint."""
    result: dict[str, Mapping[str, Any]] = {}
    unique_fingerprints = sorted({fingerprint for fingerprint in fingerprints if fingerprint})
    for chunk in chunked_values(unique_fingerprints):
        rows = (
            conn.execute(
                select(
                    transactions_table.c.id,
                    transactions_table.c.fingerprint,
                    transactions_table.c.tx_date,
                    transactions_table.c.description,
                    transactions_table.c.amount,
                    transactions_table.c.account_id,
                    accounts_table.c.name.label("account_name"),
                )
                .select_from(
                    transactions_table.outerjoin(
                        accounts_table,
                        accounts_table.c.id == transactions_table.c.account_id,
                    )
                )
                .where(transactions_table.c.fingerprint.in_(chunk))
                .order_by(transactions_table.c.id)
            )
            .mappings()
            .fetchall()
        )
        for row in rows:
            result[str(row["fingerprint"])] = dict(row)
    return result


def existing_transactions_by_details(
    conn: CoreConnection,
    transactions: Sequence[Mapping[str, Any]],
    existing_accounts: Mapping[str, Mapping[str, Any]],
) -> dict[tuple[int | None, date, str, Decimal] | None, Mapping[str, Any]]:
    """Return existing transactions keyed by natural duplicate details."""
    result: dict[tuple[int | None, date, str, Decimal] | None, Mapping[str, Any]] = {}
    expected_keys = {
        duplicate_details_key(transaction, existing_accounts)
        for transaction in transactions
        if duplicate_details_key(transaction, existing_accounts) is not None
    }
    # Three IN predicates must share the connection's bind-parameter budget.
    chunk_size = QUERY_CHUNK_SIZE // 3
    for index in range(0, len(transactions), chunk_size):
        chunk = transactions[index : index + chunk_size]
        rows = conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.fingerprint,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transactions_table.c.account_id,
                accounts_table.c.name.label("account_name"),
            )
            .select_from(
                transactions_table.outerjoin(accounts_table, accounts_table.c.id == transactions_table.c.account_id)
            )
            .where(
                transactions_table.c.tx_date.in_({row["tx_date"] for row in chunk}),
                transactions_table.c.description.in_({row["description"] for row in chunk}),
                transactions_table.c.amount.in_({row["amount"] for row in chunk}),
            )
            .order_by(transactions_table.c.id)
        ).mappings()
        for row in rows:
            row_key = (
                row["account_id"],
                coerce_date(row["tx_date"]),
                str(row["description"]),
                money_to_decimal(row["amount"]),
            )
            if row_key in expected_keys and row_key not in result:
                result[row_key] = dict(row)
    return result


def duplicate_details_key(
    transaction: Mapping[str, Any],
    existing_accounts: Mapping[str, Mapping[str, Any]],
) -> tuple[int | None, date, str, Decimal] | None:
    """Return the natural duplicate key for a parsed backup transaction."""
    account_name = str(transaction["account_name"])
    if account_name:
        existing_account = existing_accounts.get(normalize_name_key(account_name))
        if existing_account is None:
            return None
        account_id = int(existing_account["id"])
    else:
        account_id = None
    return (
        account_id,
        transaction["tx_date"],
        str(transaction["description"]),
        transaction["amount"],
    )


def remember_desired_account(
    desired_accounts: dict[str, dict[str, str]],
    errors: list[str],
    name: str,
    account_type: str,
    paid_from_account_name: str,
) -> None:
    """Record account metadata from the backup and report conflicts."""
    account_key = normalize_name_key(name)
    desired = {
        "name": name,
        "account_type": account_type,
        "paid_from_account_name": paid_from_account_name,
    }
    existing = desired_accounts.get(account_key)
    if existing is None:
        desired_accounts[account_key] = desired
        return

    if existing["account_type"] != desired["account_type"] or normalize_name_key(
        existing["paid_from_account_name"]
    ) != normalize_name_key(desired["paid_from_account_name"]):
        errors.append(gettext('Account "{name}" has conflicting metadata in the backup CSV.', name=existing["name"]))


def existing_accounts_by_key(conn: CoreConnection) -> dict[str, Mapping[str, Any]]:
    """Return existing accounts keyed by the same normalized key used by imports."""
    paid_from_accounts = accounts_table.alias("paid_from_accounts")
    rows = (
        conn.execute(
            select(
                accounts_table.c.id,
                accounts_table.c.name,
                accounts_table.c.account_type,
                accounts_table.c.paid_from_account_id,
                paid_from_accounts.c.name.label("paid_from_account_name"),
            )
            .select_from(
                accounts_table.outerjoin(
                    paid_from_accounts,
                    paid_from_accounts.c.id == accounts_table.c.paid_from_account_id,
                )
            )
            .order_by(accounts_table.c.id)
        )
        .mappings()
        .fetchall()
    )
    return {normalize_name_key(row["name"]): dict(row) for row in rows}


def build_named_reference_previews(
    conn: CoreConnection,
    table: Any,
    names: Sequence[str],
) -> tuple[BackupReferencePreview, ...]:
    """Return create/existing previews for category or tag labels."""
    labels = unique_clean_labels(names)
    existing = existing_named_rows_by_key(conn, table, labels)
    previews: list[BackupReferencePreview] = []
    for label in labels:
        row = existing.get(normalize_name_key(label))
        previews.append(
            BackupReferencePreview(
                name=label,
                action=PREVIEW_ACTION_EXISTING if row is not None else PREVIEW_ACTION_CREATE,
                existing_id=int(row["id"]) if row is not None else None,
            )
        )
    return tuple(previews)


def build_merchant_previews(
    conn: CoreConnection,
    merchant_names: Sequence[str],
) -> tuple[BackupReferencePreview, ...]:
    """Return create/existing previews for merchant keys from the backup."""
    merchant_keys = unique_merchant_keys(merchant_names)
    existing = existing_merchants_by_key(conn, merchant_keys)
    previews: list[BackupReferencePreview] = []
    for merchant_key in merchant_keys:
        row = existing.get(merchant_key)
        previews.append(
            BackupReferencePreview(
                name=merchant_key,
                action=PREVIEW_ACTION_EXISTING if row is not None else PREVIEW_ACTION_CREATE,
                existing_id=int(row["id"]) if row is not None else None,
            )
        )
    return tuple(previews)


def existing_named_rows_by_key(
    conn: CoreConnection,
    table: Any,
    names: Sequence[str],
) -> dict[str, Mapping[str, Any]]:
    """Return taxonomy rows keyed by normalized visible name."""
    keys = [normalize_name_key(name) for name in names]
    if not keys:
        return {}

    result: dict[str, Mapping[str, Any]] = {}
    for chunk in chunked_values(keys):
        rows = conn.execute(
            select(table.c.id, table.c.name, table.c.name_key).where(table.c.name_key.in_(chunk))
        ).mappings()
        result.update({row["name_key"]: dict(row) for row in rows})
    return result


def existing_merchants_by_key(
    conn: CoreConnection,
    merchant_keys: Sequence[str],
) -> dict[str, Mapping[str, Any]]:
    """Return merchant rows keyed by deterministic merchant key."""
    if not merchant_keys:
        return {}

    result: dict[str, Mapping[str, Any]] = {}
    for chunk in chunked_values(merchant_keys):
        rows = conn.execute(
            select(merchants_table.c.id, merchants_table.c.merchant_key).where(
                merchants_table.c.merchant_key.in_(chunk)
            )
        ).mappings()
        result.update({row["merchant_key"]: dict(row) for row in rows})
    return result


def category_names_from_transactions(transactions: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Return category labels referenced by parsed backup transactions."""
    return tuple(str(transaction["category"]) for transaction in transactions if transaction["category"])


def tag_names_from_transactions(transactions: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Return tag labels referenced by parsed backup transactions."""
    return tuple(tag for transaction in transactions for tag in transaction["tags"])


def merchant_names_from_transactions(transactions: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Return merchant labels referenced by parsed backup transactions."""
    return tuple(str(transaction["merchant_name"]) for transaction in transactions if transaction["merchant_name"])


def unique_clean_labels(names: Sequence[str]) -> tuple[str, ...]:
    """Return distinct display labels in first-seen order."""
    labels: list[str] = []
    seen: set[str] = set()
    for name in names:
        label = clean_label(name)
        key = normalize_name_key(label)
        if label and key not in seen:
            labels.append(label)
            seen.add(key)
    return tuple(labels)


def unique_merchant_keys(names: Sequence[str]) -> tuple[str, ...]:
    """Return distinct merchant keys in first-seen order."""
    merchant_keys: list[str] = []
    seen: set[str] = set()
    for name in names:
        merchant_key = normalize_merchant(name).merchant_key
        if merchant_key and merchant_key not in seen:
            merchant_keys.append(merchant_key)
            seen.add(merchant_key)
    return tuple(merchant_keys)


def parse_account_type(row: Mapping[str, Any], line_number: int) -> str:
    """Return a validated account type from a backup row."""
    account_type = optional_text(row, "account_type").strip() or ACCOUNT_TYPE_CHECKING
    if account_type not in ACCOUNT_TYPES:
        row_error(
            line_number,
            "{column} must be one of: {values}.",
            column="account_type",
            values=", ".join(sorted(ACCOUNT_TYPES)),
        )
    return account_type


def parse_enum_field(
    row: Mapping[str, Any],
    column: str,
    allowed_values: Sequence[str] | Mapping[str, Any],
    default: str | None,
    line_number: int,
) -> str:
    """Return a validated enum-like CSV field."""
    text = optional_text(row, column).strip()
    if not text and default is not None:
        return default
    allowed_labels = ", ".join(sorted(allowed_values))
    if text not in allowed_values:
        row_error(line_number, "{column} must be one of: {values}.", column=column, values=allowed_labels)
    return text


def parse_tag_names(row: Mapping[str, Any], line_number: int) -> tuple[str, ...]:
    """Return distinct tag names from a JSON list without treating separators as data boundaries."""
    text = optional_text(row, "tags").strip()
    if not text:
        return ()
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        row_error(line_number, "{column} must contain valid JSON.", column="tags")
    if not isinstance(loaded, list):
        row_error(line_number, "{column} must be a JSON list.", column="tags")
    names: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(loaded, start=1):
        if not isinstance(item, str):
            row_error(line_number, "{column} item {index} must be text.", column="tags", index=index)
        name = clean_label(item)
        if not name:
            row_error(line_number, "Tag {index} requires a name.", index=index)
        key = normalize_name_key(name)
        if key in seen:
            continue
        names.append(name)
        seen.add(key)
    return tuple(names)


def parse_reimbursement_allocations(
    row: Mapping[str, Any],
    line_number: int,
) -> tuple[dict[str, Any], ...]:
    """Validate credit-owned reimbursement links using portable expense fingerprints."""
    column = "reimbursement_allocations"
    text = optional_text(row, column).strip()
    if not text:
        return ()
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        row_error(line_number, "{column} must contain valid JSON.", column=column)
    if not isinstance(loaded, list):
        row_error(line_number, "{column} must be a JSON list.", column=column)

    allocations: list[dict[str, Any]] = []
    for index, item in enumerate(loaded, start=1):
        if not isinstance(item, Mapping):
            row_error(line_number, "{column} item {index} must be an object.", column=column, index=index)
        expense_fingerprint = item.get("expense_fingerprint")
        if not isinstance(expense_fingerprint, str) or not expense_fingerprint.strip():
            row_error(
                line_number,
                "{column} is required.",
                column=gettext(
                    "{column} item {index}: {field}", column=column, index=index, field="expense_fingerprint"
                ),
            )
        allocation: dict[str, Any] = {
            "expense_fingerprint": expense_fingerprint.strip(),
            "amount": required_money_value(
                item.get("amount"),
                line_number,
                gettext("{column} item {index}: {field}", column=column, index=index, field="amount"),
            ),
        }
        allocations.append(allocation)
    return tuple(allocations)


def parse_required_date(row: Mapping[str, Any], column: str, line_number: int) -> date:
    """Return a required ISO date field."""
    text = required_text(row, column, line_number).strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        row_error(line_number, "{column} must be an ISO date.", column=column)


def parse_required_money(row: Mapping[str, Any], column: str, line_number: int) -> Decimal:
    """Return a required fixed-scale money value."""
    return required_money_value(required_text(row, column, line_number), line_number, column)


def required_money_value(value: object, line_number: int, label: str) -> Decimal:
    """Return a parsed required fixed-scale money value."""
    try:
        amount = Decimal(backup_import_value(value).strip())
        if not amount.is_finite() or abs(amount) >= Decimal("1000000000000"):
            row_error(line_number, "{column} must be a valid money amount.", column=label)
        rounded = quantize_money(amount)
    except (ValueError, InvalidOperation):
        row_error(line_number, "{column} must be a valid money amount.", column=label)
    if rounded is None or rounded != amount:
        row_error(line_number, "{column} must have at most two decimal places.", column=label)
    return rounded


def parse_bool_int(row: Mapping[str, Any], column: str, line_number: int) -> int:
    """Return a persisted boolean integer from a CSV field."""
    text = optional_text(row, column).strip().lower()
    if text in {"", "0", "false", "no"}:
        return 0
    if text in {"1", "true", "yes"}:
        return 1
    row_error(line_number, "{column} must be 0 or 1.", column=column)


def parse_optional_probability(row: Mapping[str, Any], column: str, line_number: int) -> float | None:
    """Return an optional probability value constrained to the persisted range."""
    text = optional_text(row, column).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        row_error(line_number, "{column} must be a number between 0 and 1.", column=column)
    if not isfinite(value) or value < 0 or value > 1:
        row_error(line_number, "{column} must be a number between 0 and 1.", column=column)
    return value


def parse_optional_timestamp(row: Mapping[str, Any], column: str, line_number: int) -> str | None:
    """Return an optional validated timestamp string."""
    return validate_timestamp_value(optional_text(row, column), line_number, column)


def validate_timestamp_value(value: object, line_number: int, label: str) -> str | None:
    """Validate an optional ISO-like timestamp and return its text value."""
    text = backup_import_value(value).strip()
    if not text:
        return None
    candidate = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        row_error(line_number, "{column} must be an ISO timestamp.", column=label)
    return text


def required_text(row: Mapping[str, Any], column: str, line_number: int) -> str:
    """Return a required restored CSV cell value."""
    text = optional_text(row, column)
    if not text.strip():
        row_error(line_number, "{column} is required.", column=column)
    return text


def optional_text(row: Mapping[str, Any], column: str) -> str:
    """Return a restored CSV cell value."""
    return backup_import_value(row.get(column))


def optional_label(row: Mapping[str, Any], column: str) -> str:
    """Return a normalized account-style label from a CSV field."""
    text = optional_text(row, column)
    return normalize_account_name(text) if text.strip() else ""


def optional_category_label(row: Mapping[str, Any], column: str) -> str:
    """Return a normalized category label from a CSV field."""
    return clean_category_name(optional_text(row, column))


def backup_import_value(value: object) -> str:
    """Restore text written by ``csv_export_value`` while preserving blanks."""
    text = "" if value is None else str(value)
    if text.startswith("''"):
        return text[1:]
    if text.startswith("'") and text[1:].startswith(CSV_FORMULA_PREFIXES):
        return text[1:]
    return text


def backup_row_has_values(row: Mapping[str, Any]) -> bool:
    """Return whether a CSV row contains at least one import-relevant value."""
    return any(backup_import_value(row.get(column)).strip() for column in TRANSACTION_BACKUP_COLUMNS)


def chunked_values(values: Sequence[str], size: int = QUERY_CHUNK_SIZE) -> Sequence[Sequence[str]]:
    """Return stable chunks for SQL ``IN`` predicates."""
    return [values[index : index + size] for index in range(0, len(values), size)]


def row_error(line_number: int, message: str, **params: Any) -> NoReturn:
    """Raise a row-level backup CSV validation error."""
    raise BackupCsvRowError(
        gettext("Row {line_number}: {detail}", line_number=line_number, detail=gettext(message, **params))
    )
