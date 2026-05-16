"""Tests for transaction import deduplication helpers."""

import pytest
from sqlalchemy import insert

from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.statements.importer import transaction_fingerprint
from finance_app.modules.transactions.importer import filter_new_transactions


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
