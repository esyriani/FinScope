"""Integration tests for Interac e-Transfer history imports."""

from sqlalchemy import insert, select

from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    categories as categories_table,
)
from finance_app.database.tables import (
    category_rules as category_rules_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.merchants.repository import get_or_create_merchant_for_name
from finance_app.modules.upload.workflow import (
    import_transactions,
    restore_interac_undo_state,
    upload_result_message,
)


def create_account(conn, name="Checking", account_type="checking"):
    """Create an account row for Interac import tests."""
    return conn.execute(insert(accounts_table).values(name=name, account_type=account_type)).inserted_primary_key[0]


def create_interac_match(conn, account_id, description, amount, fingerprint):
    """Create a transaction row that can be enriched by Interac history."""
    return conn.execute(
        insert(transactions_table).values(
            account_id=account_id,
            tx_date="2026-05-09" if "undo" not in fingerprint else "2026-02-02",
            description=description,
            amount=amount,
            category="UNKNOWN",
            needs_review=1,
            category_source="unknown",
            fingerprint=fingerprint,
        )
    ).inserted_primary_key[0]


def transaction_rows(conn):
    """Return transaction rows used by Interac import assertions."""
    return (
        conn.execute(
            select(
                transactions_table.c.account_id,
                transactions_table.c.description,
                transactions_table.c.amount,
                transactions_table.c.category,
                transactions_table.c.merchant_id,
            ).order_by(transactions_table.c.id)
        )
        .mappings()
        .fetchall()
    )


def test_interac_history_enriches_existing_checking_transaction(app):
    """Verify Interac history updates a matching checking transaction instead of inserting a duplicate."""
    del app
    with db_core_transaction() as conn:
        account_id = create_account(conn)
        merchant = get_or_create_merchant_for_name(conn, "Kiet Menage")
        food_id = conn.execute(select(categories_table.c.id).where(categories_table.c.name == "Food")).scalar_one()
        conn.execute(
            insert(category_rules_table).values(
                merchant_id=merchant["id"],
                keyword="Kiet Menage",
                category="Food",
                category_id=food_id,
            )
        )
        create_interac_match(conn, account_id, "Envoi - VFC ***abc", 350.00, "checking-interac-sent")

        undo_state = {}
        inserted, skipped, ignored = import_transactions(
            conn,
            statement_id=10,
            account_id=account_id,
            statement_type="interac_etransfer",
            extension="csv",
            raw_text="Date Sent,Recipient,Amount,Method,Status\n08-May-26,Kiet Menage,$350.00,Mobile,DepositedGo to Details\n",
            undo_state=undo_state,
        )

        rows = transaction_rows(conn)
        assert (inserted, skipped, ignored) == (1, 0, 0)
        assert len(rows) == 1
        assert rows[0]["description"] == "Kiet Menage"
        assert rows[0]["amount"] == 350.00
        assert rows[0]["category"] == "Food"
        assert rows[0]["merchant_id"] == merchant["id"]
        assert undo_state["updated_transactions"][0]["description"] == "Envoi - VFC ***abc"


def test_interac_history_ignores_unmatched_rows(app):
    """Verify unmatched Interac rows do not create standalone ledger transactions."""
    del app
    with db_core_transaction() as conn:
        account_id = create_account(conn)

        inserted, skipped, ignored = import_transactions(
            conn,
            statement_id=11,
            account_id=account_id,
            statement_type="interac_etransfer",
            extension="csv",
            raw_text="Date Deposited,Received From,Amount,Method,Status\n02-Jan-23,CHARLES DEMERS,$1250.00,Email,Autodeposited\n",
        )

        count = conn.execute(select(transactions_table.c.id)).fetchall()
        assert (inserted, skipped, ignored) == (0, 0, 1)
        assert count == []


def test_interac_result_message_explains_skipped_and_ignored_rows():
    """Verify Interac import reports explain skipped and ignored row causes."""
    message = upload_result_message(
        "interac_etransfer",
        "csv",
        inserted_count=29,
        skipped_count=1,
        ignored_count=76,
    )

    assert "Skipped 1 ambiguous match because each matched more than one possible checking transaction." in message
    assert "Ignored 76 rows that were cancelled, non-deposited" in message
    assert "no matching checking ledger transaction yet" in message
    assert "Import matching checking statements first" in message


def test_interac_history_falls_back_to_existing_ledger_account(app):
    """Verify empty Interac history accounts can enrich matching ledger transactions."""
    del app
    with db_core_transaction() as conn:
        ledger_account_id = create_account(conn, "TD", "checking")
        history_account_id = create_account(conn, "TD Interac Sent", "checking")
        create_interac_match(conn, ledger_account_id, "Envoi - VFC ***abc", 350.00, "td-interac-sent")

        inserted, skipped, ignored = import_transactions(
            conn,
            statement_id=14,
            account_id=history_account_id,
            statement_type="interac_etransfer",
            extension="csv",
            raw_text="Date Sent,Recipient,Amount,Method,Status\n08-May-26,Kiet Menage,$350.00,Mobile,DepositedGo to Details\n",
        )

        row = transaction_rows(conn)[0]
        assert (inserted, skipped, ignored) == (1, 0, 0)
        assert row["account_id"] == ledger_account_id
        assert row["description"] == "Kiet Menage"
        assert row["amount"] == 350.00


def test_interac_history_direction_override_enriches_generic_export(app):
    """Verify direction override signs and matches generic positive-amount Interac rows."""
    del app
    with db_core_transaction() as conn:
        account_id = create_account(conn)
        create_interac_match(conn, account_id, "Recept - VFC ***abc", -125.00, "checking-interac-received")

        inserted, skipped, ignored = import_transactions(
            conn,
            statement_id=13,
            account_id=account_id,
            statement_type="interac_etransfer",
            extension="csv",
            raw_text="Date,Name,Amount,Status\n08-May-26,Alex Buyer,$125.00,Autodeposited\n",
            interac_direction="received",
        )

        row = transaction_rows(conn)[0]
        assert (inserted, skipped, ignored) == (1, 0, 0)
        assert row["description"] == "Alex Buyer"
        assert row["amount"] == -125.00


def test_interac_enrichment_undo_state_restores_original_transaction(app):
    """Verify Interac enrichment can be restored by the upload job undo path."""
    del app
    with db_core_transaction() as conn:
        account_id = create_account(conn)
        create_interac_match(conn, account_id, "Envoi - VFC ***abc", 8.01, "checking-undo")
        undo_state = {}
        import_transactions(
            conn,
            statement_id=12,
            account_id=account_id,
            statement_type="interac_etransfer",
            extension="csv",
            raw_text="Date Sent,Recipient,Amount,Method,Status\n31-Jan-26,Joseph Electro,$8.01,Email,DepositedGo to Details\n",
            undo_state=undo_state,
        )

        restored_count = restore_interac_undo_state(conn, undo_state)
        row = transaction_rows(conn)[0]

        assert restored_count == 1
        assert row["description"] == "Envoi - VFC ***abc"
        assert row["merchant_id"] is None
