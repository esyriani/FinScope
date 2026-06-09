"""Statement and transaction import helpers.

Provides SQLAlchemy Core deduplication helpers for imported transaction rows.
Callers manage database transactions and pass Core connections bound to the
application schema.
"""

from collections.abc import Iterable, MutableMapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection as CoreConnection

from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.statements.importer import transaction_fingerprint


def require_core_connection(conn: object) -> None:
    """Validate that transaction import helpers receive a Core connection."""
    if not isinstance(conn, CoreConnection):
        raise TypeError("Transaction import helpers require a SQLAlchemy Core connection.")


def filter_new_transactions(
    conn: CoreConnection,
    transactions: Iterable[MutableMapping[str, Any]],
    account_id: int | None,
) -> tuple[list[MutableMapping[str, Any]], int]:
    """Filter new transactions."""
    require_core_connection(conn)

    unique_transactions: list[MutableMapping[str, Any]] = []
    fingerprints: list[str] = []
    seen_in_batch: set[str] = set()
    skipped_count = 0

    for tx in transactions:
        fingerprint = transaction_fingerprint(tx, account_id)
        tx["fingerprint"] = fingerprint

        if fingerprint in seen_in_batch:
            skipped_count += 1
            continue

        seen_in_batch.add(fingerprint)
        fingerprints.append(fingerprint)
        unique_transactions.append(tx)

    existing_fingerprints = get_existing_transaction_fingerprints(conn, fingerprints)
    new_transactions: list[MutableMapping[str, Any]] = []

    for tx in unique_transactions:
        if tx["fingerprint"] in existing_fingerprints:
            skipped_count += 1
        else:
            new_transactions.append(tx)

    return new_transactions, skipped_count


def get_existing_transaction_fingerprints(conn: CoreConnection, fingerprints: Iterable[str]) -> set[str]:
    """Return existing transaction fingerprints."""
    require_core_connection(conn)

    existing: set[str] = set()
    chunk_size = 900
    fingerprint_values = list(fingerprints)

    for index in range(0, len(fingerprint_values), chunk_size):
        chunk = fingerprint_values[index : index + chunk_size]
        if not chunk:
            continue

        rows = conn.execute(
            select(transactions_table.c.fingerprint).where(transactions_table.c.fingerprint.in_(chunk))
        ).fetchall()
        existing.update(row._mapping["fingerprint"] for row in rows)

    return existing
