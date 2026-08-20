"""Tests for upload transaction import workflow edge cases."""

import pytest
from sqlalchemy import event, insert, select, text
from tests.support.upload import queue_statement_import_attempt

from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    categories as categories_table,
)
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.database.tables import (
    statement_types as statement_types_table,
)
from finance_app.database.tables import (
    statements as statements_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import rename_category, resolve_category_id
from finance_app.modules.upload import ai_workflow as upload_ai_workflow
from finance_app.modules.upload import workflow as upload_workflow
from finance_app.modules.upload.repository import new_statement_import_token, reset_statement_import_state


def create_statement(conn, filename="workflow.csv", account_name="Personal"):
    """Create an account and statement for upload workflow tests."""
    account_id = conn.execute(
        text("""
        INSERT INTO accounts (name)
        VALUES (:p0)
        """),
        {"p0": account_name},
    ).lastrowid
    statement_type_id = conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """)).fetchone()._mapping["id"]
    statement_id = conn.execute(
        text("""
        INSERT INTO statements (account_id, statement_type_id, filename, checksum, raw_text)
        VALUES (:p0, :p1, :p2, :p3, '')
        """),
        {"p0": account_id, "p1": statement_type_id, "p2": filename, "p3": f"checksum-{filename}"},
    ).lastrowid
    conn.commit()
    return account_id, statement_id


def create_core_statement(conn, filename="workflow.csv"):
    """Create an account and statement for Core upload workflow tests."""
    account_id = conn.execute(insert(accounts_table).values(name="Personal")).inserted_primary_key[0]
    statement_type_id = conn.execute(
        select(statement_types_table.c.id)
        .where(statement_types_table.c.active == 1)
        .order_by(statement_types_table.c.id)
        .limit(1)
    ).scalar_one()
    statement_id = conn.execute(
        insert(statements_table).values(
            account_id=account_id,
            statement_type_id=statement_type_id,
            filename=filename,
            checksum=f"checksum-{filename}",
            raw_text="",
        )
    ).inserted_primary_key[0]
    return account_id, statement_id


def categorized_unknowns(transactions, conn=None):
    """Return deterministic unknown categorization output."""
    del conn
    for tx in transactions:
        tx.update(
            {
                "category": "UNKNOWN",
                "needs_review": 1,
                "category_source": "unknown",
                "category_confidence": None,
                "category_rule_id": None,
                "categorized_at": None,
                "reviewed_at": None,
                "tags": [],
            }
        )
    return transactions


def categorized_food(transactions, conn=None):
    """Return deterministic Food categorization output."""
    del conn
    for tx in transactions:
        tx.update(
            {
                "category": "Food",
                "needs_review": 0,
                "category_source": "rule",
                "category_confidence": 1.0,
                "category_rule_id": None,
                "categorized_at": "2026-05-15T00:00:00Z",
                "reviewed_at": None,
                "tags": [],
            }
        )
    return transactions


def run_statement_import_job(conn, statement_id, account_id, statement_type, extension, raw_text, **kwargs):
    """Queue and run one statement import job with a matching claim token."""
    import_token = queue_statement_import_attempt(conn, statement_id)
    return upload_workflow.import_statement_transactions_job(
        statement_id,
        account_id,
        statement_type,
        extension,
        raw_text,
        import_token,
        **kwargs,
    )


def test_import_transactions_counts_ignored_csv_rows(app, monkeypatch):
    """Verify ignored parser rows are reported with successful inserts."""
    del app
    monkeypatch.setattr(
        upload_workflow,
        "parse_csv_transactions",
        lambda raw_text, statement_type, **kwargs: {
            "transactions": [
                {
                    "tx_date": "2026-01-02",
                    "description": "Unknown Shop",
                    "amount": 12.34,
                }
            ],
            "ignored_rows": 2,
        },
    )
    monkeypatch.setattr(upload_workflow, "categorize_transactions", categorized_unknowns)

    with db_core_transaction() as conn:
        account_id, statement_id = create_core_statement(conn, "ignored-rows.csv")
        result = upload_workflow.import_transactions(
            conn,
            statement_id,
            account_id,
            "credit_card",
            "csv",
            "raw",
        )

        row = conn.execute(
            select(
                transactions_table.c.description,
                transactions_table.c.category,
                transactions_table.c.needs_review,
                merchants_table.c.merchant_key.label("merchant_name"),
            )
            .join(merchants_table, merchants_table.c.id == transactions_table.c.merchant_id)
            .where(transactions_table.c.statement_id == statement_id)
        ).fetchone()

    assert result == (1, 0, 2)
    assert tuple(row) == ("Unknown Shop", "UNKNOWN", 1, "UNKNOWN SHOP")


def test_import_transactions_preserves_repeated_same_value_statement_rows(app, monkeypatch):
    """Verify repeated statement rows import once each while replay stays idempotent."""
    del app
    monkeypatch.setattr(upload_workflow, "categorize_transactions", categorized_unknowns)
    raw_csv = "\n".join(
        [
            "Date,Description,Amount",
            "2026-01-02,Cafe Bistro,8.50",
            "2026-01-02,Cafe Bistro,8.50",
        ]
    )

    with db_core_transaction() as conn:
        account_id, statement_id = create_core_statement(conn, "repeated-rows.csv")
        first_result = upload_workflow.import_transactions(
            conn,
            statement_id,
            account_id,
            "credit_card",
            "csv",
            raw_csv,
        )
        second_result = upload_workflow.import_transactions(
            conn,
            statement_id,
            account_id,
            "credit_card",
            "csv",
            raw_csv,
        )
        rows = (
            conn.execute(
                select(transactions_table.c.description, transactions_table.c.fingerprint)
                .where(transactions_table.c.statement_id == statement_id)
                .order_by(transactions_table.c.id)
            )
            .mappings()
            .fetchall()
        )

    assert first_result == (2, 0, 0)
    assert second_result == (0, 2, 0)
    assert [row["description"] for row in rows] == ["Cafe Bistro", "Cafe Bistro"]
    assert len({row["fingerprint"] for row in rows}) == 2


def test_import_transactions_counts_sqlite_integrity_duplicate_skips(app, monkeypatch):
    """Verify insert-time unique fingerprint collisions increment skipped count."""
    del app
    duplicate_rows = [
        {
            "tx_date": "2026-01-02",
            "description": "Duplicate Shop",
            "amount": 12.34,
            "fingerprint": "forced-duplicate-fingerprint",
        },
        {
            "tx_date": "2026-01-03",
            "description": "Duplicate Shop Later",
            "amount": 56.78,
            "fingerprint": "forced-duplicate-fingerprint",
        },
    ]
    monkeypatch.setattr(
        upload_workflow,
        "parse_csv_transactions",
        lambda raw_text, statement_type, **kwargs: {"transactions": [{"placeholder": True}], "ignored_rows": 0},
    )
    monkeypatch.setattr(
        upload_workflow,
        "filter_new_transactions",
        lambda conn, transactions, account_id, statement_id: ([dict(row) for row in duplicate_rows], 0),
    )
    monkeypatch.setattr(upload_workflow, "categorize_transactions", categorized_unknowns)

    with db_core_transaction() as conn:
        account_id, statement_id = create_core_statement(conn, "integrity-skip.csv")
        result = upload_workflow.import_transactions(
            conn,
            statement_id,
            account_id,
            "credit_card",
            "csv",
            "raw",
        )

        count = conn.execute(
            select(transactions_table.c.id).where(transactions_table.c.statement_id == statement_id)
        ).fetchall()

    assert result == (1, 1, 0)
    assert len(count) == 1


def test_import_transactions_wraps_insert_time_duplicates_in_savepoints(app, monkeypatch):
    """Verify insert-time duplicates roll back only their row insert attempt."""
    del app
    duplicate_rows = [
        {
            "tx_date": "2026-01-02",
            "description": "Existing Shop",
            "amount": 12.34,
            "fingerprint": "savepoint-existing-fingerprint",
        },
        {
            "tx_date": "2026-01-03",
            "description": "Fresh Shop",
            "amount": 56.78,
            "fingerprint": "savepoint-fresh-fingerprint",
        },
    ]
    monkeypatch.setattr(
        upload_workflow,
        "parse_csv_transactions",
        lambda raw_text, statement_type, **kwargs: {"transactions": [{"placeholder": True}], "ignored_rows": 0},
    )
    monkeypatch.setattr(
        upload_workflow,
        "filter_new_transactions",
        lambda conn, transactions, account_id, statement_id: ([dict(row) for row in duplicate_rows], 0),
    )
    monkeypatch.setattr(upload_workflow, "categorize_transactions", categorized_unknowns)

    with db_core_transaction() as conn:
        account_id, statement_id = create_core_statement(conn, "savepoint-duplicate.csv")
        conn.execute(
            insert(transactions_table).values(
                tx_date="2026-01-01",
                description="Previously imported shop",
                amount=12.34,
                category="UNKNOWN",
                fingerprint="savepoint-existing-fingerprint",
            )
        )
        statements = []

        def capture_sql(conn, cursor, statement, parameters, context, executemany):
            """Capture transaction-control SQL emitted by nested inserts."""
            del conn, cursor, parameters, context, executemany
            statements.append(statement.upper())

        event.listen(conn, "before_cursor_execute", capture_sql)
        try:
            result = upload_workflow.import_transactions(
                conn,
                statement_id,
                account_id,
                "credit_card",
                "csv",
                "raw",
            )
        finally:
            event.remove(conn, "before_cursor_execute", capture_sql)

        rows = conn.execute(
            select(transactions_table.c.description, transactions_table.c.fingerprint)
            .where(transactions_table.c.statement_id == statement_id)
            .order_by(transactions_table.c.id)
        ).fetchall()

    assert result == (1, 1, 0)
    assert [tuple(row) for row in rows] == [
        ("Fresh Shop", "savepoint-fresh-fingerprint"),
    ]
    assert any(statement.startswith("SAVEPOINT") for statement in statements)
    assert any(statement.startswith("ROLLBACK TO SAVEPOINT") for statement in statements)


def test_upload_import_and_llm_update_rows_follow_category_rename(app, monkeypatch):
    """Verify uploaded and LLM-updated transactions store category IDs."""
    del app
    monkeypatch.setattr(
        upload_workflow,
        "parse_csv_transactions",
        lambda raw_text, statement_type, **kwargs: {
            "transactions": [
                {
                    "tx_date": "2026-01-02",
                    "description": "Metro Grocery",
                    "amount": 12.34,
                }
            ],
            "ignored_rows": 0,
        },
    )
    monkeypatch.setattr(upload_workflow, "categorize_transactions", categorized_food)

    with db_core_transaction() as conn:
        food_id = resolve_category_id(conn, "Food")
        utilities_id = resolve_category_id(conn, "Utilities")
        account_id, statement_id = create_core_statement(conn, "identity-rename.csv")
        assert upload_workflow.import_transactions(
            conn,
            statement_id,
            account_id,
            "credit_card",
            "csv",
            "raw",
        ) == (1, 0, 0)
        llm_tx_id = conn.execute(
            insert(transactions_table).values(
                statement_id=statement_id,
                account_id=account_id,
                tx_date="2026-01-03",
                description="Hydro Quebec",
                amount=120.00,
                category="UNKNOWN",
                needs_review=1,
                fingerprint="llm-identity-rename",
            )
        ).inserted_primary_key[0]
        upload_ai_workflow.update_unknown_transaction_category(
            conn,
            {
                "id": llm_tx_id,
                "category": "Utilities",
                "needs_review": 0,
                "category_source": "ai",
                "category_confidence": 0.95,
                "category_rule_id": None,
                "categorized_at": "2026-05-15T00:00:00Z",
                "reviewed_at": None,
            },
            "UNKNOWN",
        )
        assert rename_category(conn, "Food", "Meals") == "Meals"
        assert rename_category(conn, "Utilities", "Bills") == "Bills"
        rows = conn.execute(
            select(
                transactions_table.c.description,
                transactions_table.c.category_id,
                transactions_table.c.category,
            )
            .where(transactions_table.c.statement_id == statement_id)
            .order_by(transactions_table.c.description)
        ).fetchall()
        renamed_categories = conn.execute(
            select(categories_table.c.id, categories_table.c.name).where(
                categories_table.c.id.in_([food_id, utilities_id])
            )
        ).fetchall()

    assert [tuple(row) for row in rows] == [
        ("Hydro Quebec", utilities_id, "Bills"),
        ("Metro Grocery", food_id, "Meals"),
    ]
    assert {row.id: row.name for row in renamed_categories} == {
        food_id: "Meals",
        utilities_id: "Bills",
    }


def test_import_transactions_returns_zero_counts_for_non_csv(app, monkeypatch):
    """Verify non-CSV imports are ignored by the CSV transaction importer."""
    del app
    monkeypatch.setattr(
        upload_workflow,
        "parse_csv_transactions",
        lambda *args, **kwargs: pytest.fail("Non-CSV imports must not invoke the CSV parser"),
    )

    with db_core_transaction() as conn:
        account_id, statement_id = create_core_statement(conn, "statement.txt")
        assert upload_workflow.import_transactions(
            conn,
            statement_id,
            account_id,
            "credit_card",
            "txt",
            "raw text",
        ) == (0, 0, 0)


def test_import_statement_job_records_failed_statement_status(core_conn, monkeypatch):
    """Verify failed import jobs leave the statement retryable."""
    account_id, statement_id = create_statement(core_conn, "failed.csv")
    monkeypatch.setattr(
        upload_workflow,
        "parse_csv_transactions",
        lambda raw_text, statement_type, **kwargs: (_ for _ in ()).throw(RuntimeError("parser broke")),
    )

    with pytest.raises(RuntimeError, match="parser broke"):
        run_statement_import_job(
            core_conn,
            statement_id,
            account_id,
            "credit_card",
            "csv",
            "raw",
        )

    statement = core_conn.execute(
        text("""
        SELECT import_status, import_error, imported_count
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()
    assert statement._mapping["import_status"] == "failed"
    assert statement._mapping["import_error"] == "RuntimeError: parser broke"
    assert statement._mapping["imported_count"] == 0


def test_import_statement_job_rolls_back_inserted_rows_when_finalization_fails(core_conn, monkeypatch):
    """Verify import rows and final statement state commit atomically."""
    account_id, statement_id = create_statement(core_conn, "finalize-failed.csv")
    monkeypatch.setattr(
        upload_workflow,
        "count_statement_unknown_transactions",
        lambda conn, statement_id: (_ for _ in ()).throw(RuntimeError("counter broke")),
    )

    with pytest.raises(RuntimeError, match="counter broke"):
        run_statement_import_job(
            core_conn,
            statement_id,
            account_id,
            "credit_card",
            "csv",
            "Date,Description,Amount\n2026-01-02,UNKNOWN SHOP,12.34\n",
        )

    statement = core_conn.execute(
        text("""
        SELECT import_status, import_error, imported_count
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()
    transaction_count = (
        core_conn.execute(
            text("""
        SELECT COUNT(*) AS count
        FROM transactions
        WHERE statement_id = :p0
        """),
            {"p0": statement_id},
        )
        .fetchone()
        ._mapping["count"]
    )

    assert statement._mapping["import_status"] == "failed"
    assert statement._mapping["import_error"] == "RuntimeError: counter broke"
    assert statement._mapping["imported_count"] == 0
    assert transaction_count == 0


def test_failed_import_retry_does_not_leave_orphan_transactions(core_conn, monkeypatch):
    """Verify a failed finalization can be retried without orphaned rows."""
    account_id, statement_id = create_statement(core_conn, "retry-after-failure.csv")
    attempts = {"count": 0}

    def count_unknowns_once_then_succeed(conn, counted_statement_id):
        """Fail after row insertion on the first import attempt only."""
        del conn
        assert counted_statement_id == statement_id
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("counter broke")
        return 0

    monkeypatch.setattr(
        upload_workflow,
        "count_statement_unknown_transactions",
        count_unknowns_once_then_succeed,
    )

    raw_csv = "Date,Description,Amount\n2026-01-02,Retry Shop,12.34\n"
    with pytest.raises(RuntimeError, match="counter broke"):
        run_statement_import_job(
            core_conn,
            statement_id,
            account_id,
            "credit_card",
            "csv",
            raw_csv,
        )

    failed_count = (
        core_conn.execute(
            text("""
        SELECT COUNT(*) AS count
        FROM transactions
        WHERE statement_id = :p0
        """),
            {"p0": statement_id},
        )
        .fetchone()
        ._mapping["count"]
    )
    failed_statement = core_conn.execute(
        text("""
        SELECT import_status, imported_count
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()

    message = run_statement_import_job(
        core_conn,
        statement_id,
        account_id,
        "credit_card",
        "csv",
        raw_csv,
    )

    rows = core_conn.execute(
        text("""
        SELECT description, amount
        FROM transactions
        WHERE statement_id = :p0
        """),
        {"p0": statement_id},
    ).fetchall()
    completed_statement = core_conn.execute(
        text("""
        SELECT import_status, import_error, imported_count, skipped_count, ignored_count
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()

    assert failed_count == 0
    assert tuple(failed_statement) == ("failed", 0)
    assert "Added 1 transactions" in message
    assert [tuple(row) for row in rows] == [("Retry Shop", 12.34)]
    assert tuple(completed_statement) == ("completed", None, 1, 0, 0)


def test_import_statement_job_noops_when_attempt_is_already_claimed(core_conn):
    """Verify duplicate workers cannot rerun a completed statement import attempt."""
    account_id, statement_id = create_statement(core_conn, "duplicate-claim.csv")
    raw_csv = "Date,Description,Amount\n2026-01-02,Claimed Shop,12.34\n"
    import_token = queue_statement_import_attempt(core_conn, statement_id)

    first_message = upload_workflow.import_statement_transactions_job(
        statement_id,
        account_id,
        "credit_card",
        "csv",
        raw_csv,
        import_token,
    )
    second_message = upload_workflow.import_statement_transactions_job(
        statement_id,
        account_id,
        "credit_card",
        "csv",
        raw_csv,
        import_token,
    )

    transaction_count = (
        core_conn.execute(
            text("""
        SELECT COUNT(*) AS count
        FROM transactions
        WHERE statement_id = :p0
        """),
            {"p0": statement_id},
        )
        .fetchone()
        ._mapping["count"]
    )
    statement = core_conn.execute(
        text("""
        SELECT import_status, imported_count
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()

    assert "Added 1 transactions" in first_message
    assert second_message == "Statement import was already claimed by another attempt."
    assert transaction_count == 1
    assert tuple(statement) == ("completed", 1)


def test_reprocess_statement_import_replaces_rows_after_success(core_conn):
    """Verify reprocess replaces same-fingerprint rows in the successful import transaction."""
    account_id, statement_id = create_statement(core_conn, "reprocess-success.csv")
    raw_csv = "Date,Description,Amount\n2026-01-02,Reprocess Same Shop,12.34\n"
    initial_message = run_statement_import_job(
        core_conn,
        statement_id,
        account_id,
        "credit_card",
        "csv",
        raw_csv,
    )
    original = core_conn.execute(
        text("""
        SELECT id, fingerprint
        FROM transactions
        WHERE statement_id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()

    reprocess_message = run_statement_import_job(
        core_conn,
        statement_id,
        account_id,
        "credit_card",
        "csv",
        raw_csv,
        replace_existing_transactions=True,
    )

    rows = core_conn.execute(
        text("""
        SELECT id, description, fingerprint
        FROM transactions
        WHERE statement_id = :p0
        """),
        {"p0": statement_id},
    ).fetchall()
    statement = core_conn.execute(
        text("""
        SELECT import_status, imported_count, skipped_count, ignored_count
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()

    assert "Added 1 transactions" in initial_message
    assert "Added 1 transactions" in reprocess_message
    assert len(rows) == 1
    assert rows[0]._mapping["description"] == "Reprocess Same Shop"
    assert rows[0]._mapping["fingerprint"] == original._mapping["fingerprint"]
    assert rows[0]._mapping["id"] != original._mapping["id"]
    assert tuple(statement) == ("completed", 1, 0, 0)


def test_reprocess_statement_import_failure_keeps_existing_transactions(core_conn, monkeypatch):
    """Verify failed replacement import rolls back to the previous statement rows."""
    account_id, statement_id = create_statement(core_conn, "reprocess-failure.csv")
    initial_raw_csv = "Date,Description,Amount\n2026-01-02,Old Reprocess Shop,12.34\n"
    replacement_raw_csv = "Date,Description,Amount\n2026-01-03,New Reprocess Shop,45.67\n"
    run_statement_import_job(
        core_conn,
        statement_id,
        account_id,
        "credit_card",
        "csv",
        initial_raw_csv,
    )
    original = core_conn.execute(
        text("""
        SELECT id, description, fingerprint
        FROM transactions
        WHERE statement_id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()

    def fail_after_replacement_rows_are_imported(conn, counted_statement_id):
        """Raise during final import bookkeeping after replacement rows are inserted."""
        del conn, counted_statement_id
        raise RuntimeError("replacement counter broke")

    monkeypatch.setattr(
        upload_workflow,
        "count_statement_unknown_transactions",
        fail_after_replacement_rows_are_imported,
    )

    with pytest.raises(RuntimeError, match="replacement counter broke"):
        run_statement_import_job(
            core_conn,
            statement_id,
            account_id,
            "credit_card",
            "csv",
            replacement_raw_csv,
            replace_existing_transactions=True,
        )

    rows = core_conn.execute(
        text("""
        SELECT id, description, fingerprint
        FROM transactions
        WHERE statement_id = :p0
        """),
        {"p0": statement_id},
    ).fetchall()
    statement = core_conn.execute(
        text("""
        SELECT import_status, imported_count, import_error
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()

    assert [tuple(row) for row in rows] == [tuple(original)]
    assert statement._mapping["import_status"] == "failed"
    assert statement._mapping["imported_count"] == 0
    assert "replacement counter broke" in statement._mapping["import_error"]


def test_statement_import_queue_claim_prevents_duplicate_retry_reprocess(core_conn):
    """Verify only one retry/reprocess submission can queue a statement."""
    account_id, statement_id = create_statement(core_conn, "retry-reprocess-race.csv")
    core_conn.execute(
        text("""
        UPDATE statements
        SET raw_text = 'Date,Description,Amount\n2026-01-02,RACE SHOP,12.34',
            import_status = 'completed',
            imported_count = 1
        WHERE id = :p0
        """),
        {"p0": statement_id},
    )
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            statement_id,
            account_id,
            tx_date,
            description,
            amount,
            category,
            fingerprint
        )
        VALUES (:p0, :p1, '2026-01-01', 'REPLACED RACE SHOP', 5.00, 'UNKNOWN', 'retry-reprocess-race-existing')
        """),
        {"p0": statement_id, "p1": account_id},
    )
    core_conn.commit()
    retry_token = new_statement_import_token()
    reprocess_token = new_statement_import_token()

    retry_queued = reset_statement_import_state(core_conn, statement_id, retry_token)
    reprocess_queued = reset_statement_import_state(core_conn, statement_id, reprocess_token)
    if reprocess_queued:
        core_conn.execute(text("DELETE FROM transactions WHERE statement_id = :p0"), {"p0": statement_id})
    core_conn.commit()

    transaction_count = (
        core_conn.execute(
            text("""
        SELECT COUNT(*) AS count
        FROM transactions
        WHERE statement_id = :p0
        """),
            {"p0": statement_id},
        )
        .fetchone()
        ._mapping["count"]
    )
    statement = core_conn.execute(
        text("""
        SELECT import_status, import_token
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()

    assert retry_queued is True
    assert reprocess_queued is False
    assert transaction_count == 1
    assert tuple(statement) == ("queued", retry_token)


def test_multi_account_retry_keeps_account_scoped_deduplication(core_conn, monkeypatch):
    """Verify a failed retry does not dedupe the same row across accounts."""
    account_a_id, statement_a_id = create_statement(
        core_conn,
        "account-a-retry.csv",
        account_name="Account A",
    )
    account_b_id, statement_b_id = create_statement(
        core_conn,
        "account-b-retry.csv",
        account_name="Account B",
    )
    attempts = {"count": 0}

    def fail_first_account_once(conn, counted_statement_id):
        """Fail only the first account's first finalization pass."""
        del conn
        if counted_statement_id == statement_a_id:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("account A counter broke")
        return 0

    monkeypatch.setattr(
        upload_workflow,
        "count_statement_unknown_transactions",
        fail_first_account_once,
    )
    raw_csv = "Date,Description,Amount\n2026-01-03,Shared Retry Merchant,45.67\n"

    with pytest.raises(RuntimeError, match="account A counter broke"):
        run_statement_import_job(
            core_conn,
            statement_a_id,
            account_a_id,
            "credit_card",
            "csv",
            raw_csv,
        )
    run_statement_import_job(
        core_conn,
        statement_a_id,
        account_a_id,
        "credit_card",
        "csv",
        raw_csv,
    )
    run_statement_import_job(
        core_conn,
        statement_b_id,
        account_b_id,
        "credit_card",
        "csv",
        raw_csv,
    )

    rows = core_conn.execute(text("""
        SELECT accounts.name, transactions.fingerprint, transactions.statement_id
        FROM transactions
        JOIN accounts ON accounts.id = transactions.account_id
        WHERE transactions.description = 'Shared Retry Merchant'
        ORDER BY accounts.name
        """)).mappings().fetchall()

    assert [(row["name"], row["statement_id"]) for row in rows] == [
        ("Account A", statement_a_id),
        ("Account B", statement_b_id),
    ]
    assert rows[0]["fingerprint"] != rows[1]["fingerprint"]
