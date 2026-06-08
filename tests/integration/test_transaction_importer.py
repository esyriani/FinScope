"""Tests for transaction import deduplication helpers."""

import pytest
from sqlalchemy import insert

from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.statements.importer import transaction_fingerprint
from finance_app.modules.transactions.importer import (
    filter_new_transactions,
    get_existing_transaction_fingerprints,
)


def test_filter_new_transactions_skips_batch_and_existing_fingerprint_duplicates(app):
    """Verify import dedupe handles in-batch duplicates and already imported rows."""
    del app
    account_id = 42
    existing_tx = {
        "tx_date": "2026-01-01",
        "description": "Metro Grocery",
        "amount": 20.00,
    }
    duplicate_tx = {
        "tx_date": "2026-01-02",
        "description": "Cafe Bistro",
        "amount": 12.50,
    }
    duplicate_tx_copy = dict(duplicate_tx)
    fresh_tx = {
        "tx_date": "2026-01-03",
        "description": "Hydro Quebec",
        "amount": 120.00,
    }
    with db_core_transaction() as conn:
        conn.execute(
            insert(transactions_table).values(
                tx_date=existing_tx["tx_date"],
                description=existing_tx["description"],
                amount=existing_tx["amount"],
                fingerprint=transaction_fingerprint(existing_tx, account_id),
            )
        )

        new_transactions, skipped_count = filter_new_transactions(
            conn,
            [existing_tx, duplicate_tx, duplicate_tx_copy, fresh_tx],
            account_id,
        )

    assert skipped_count == 2
    assert [tx["description"] for tx in new_transactions] == ["Cafe Bistro", "Hydro Quebec"]
    assert duplicate_tx["fingerprint"] == duplicate_tx_copy["fingerprint"]
    assert fresh_tx["fingerprint"] == transaction_fingerprint(fresh_tx, account_id)


def test_filter_new_transactions_handles_empty_batches(app):
    """Verify empty imports return an empty result without querying bad SQL."""
    del app
    with db_core_transaction() as conn:
        assert filter_new_transactions(conn, [], account_id=1) == ([], 0)


def test_filter_new_transactions_requires_core_connection():
    """Reject non-Core connections at the transaction importer boundary."""
    with pytest.raises(TypeError):
        filter_new_transactions(object(), [], account_id=1)


@pytest.mark.parametrize(
    ("candidate_tx", "candidate_account_id", "expected_descriptions", "expected_skipped"),
    [
        (
            {"tx_date": "2026-02-01", "description": "Shared Merchant", "amount": 12.34},
            1,
            [],
            1,
        ),
        (
            {"tx_date": "2026-02-01", "description": "Shared Merchant", "amount": 12.35},
            1,
            ["Shared Merchant"],
            0,
        ),
        (
            {"tx_date": "2026-02-01", "description": "Shared Merchant", "amount": 12.34},
            2,
            ["Shared Merchant"],
            0,
        ),
        (
            {"tx_date": "2026-02-01", "description": "Different Merchant", "amount": 12.34},
            1,
            ["Different Merchant"],
            0,
        ),
    ],
)
def test_filter_new_transactions_table_driven_dedup_boundaries(
    core_conn,
    candidate_tx,
    candidate_account_id,
    expected_descriptions,
    expected_skipped,
):
    """Verify import dedupe keys include account, date, description, and amount."""
    existing_tx = {
        "tx_date": "2026-02-01",
        "description": "Shared Merchant",
        "amount": 12.34,
    }
    core_conn.execute(
        insert(transactions_table).values(
            tx_date=existing_tx["tx_date"],
            description=existing_tx["description"],
            amount=existing_tx["amount"],
            fingerprint=transaction_fingerprint(existing_tx, 1),
        )
    )

    new_transactions, skipped_count = filter_new_transactions(
        core_conn,
        [dict(candidate_tx)],
        candidate_account_id,
    )

    assert skipped_count == expected_skipped
    assert [tx["description"] for tx in new_transactions] == expected_descriptions


def test_filter_new_transactions_preserves_first_occurrence_order_for_generated_duplicates(core_conn):
    """Verify in-batch dedupe keeps first rows and counts generated duplicates."""
    unique_rows = [
        {
            "tx_date": f"2026-03-{day:02d}",
            "description": f"Merchant {day:02d}",
            "amount": day + 0.25,
        }
        for day in range(1, 21)
    ]
    batch = []
    for row in unique_rows:
        batch.append(dict(row))
        if int(row["tx_date"][-2:]) % 4 == 0:
            batch.extend([dict(row), dict(row)])

    new_transactions, skipped_count = filter_new_transactions(core_conn, batch, account_id=7)

    assert skipped_count == len(batch) - len(unique_rows)
    assert [tx["description"] for tx in new_transactions] == [row["description"] for row in unique_rows]
    assert len({tx["fingerprint"] for tx in new_transactions}) == len(unique_rows)


def test_get_existing_transaction_fingerprints_checks_all_chunks(core_conn):
    """Verify existing-fingerprint lookup spans batches larger than one SQL chunk."""
    fingerprints = [f"chunked-fingerprint-{index:04d}" for index in range(1805)]
    existing = {
        fingerprints[0],
        fingerprints[899],
        fingerprints[900],
        fingerprints[1804],
    }
    core_conn.execute(
        insert(transactions_table),
        [
            {
                "tx_date": "2026-04-01",
                "description": f"Existing {index}",
                "amount": index + 0.01,
                "fingerprint": fingerprint,
            }
            for index, fingerprint in enumerate(existing)
        ],
    )

    assert get_existing_transaction_fingerprints(core_conn, fingerprints) == existing
