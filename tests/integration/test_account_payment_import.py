"""Integration tests for account-role aware payment imports."""

from decimal import Decimal

from sqlalchemy import insert, select

from finance_app.core.query import CoreFilters
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    statements as statements_table,
    statement_types as statement_types_table,
    transactions as transactions_table,
)
from finance_app.modules.accounts.repository import get_or_create_account
from finance_app.modules.dashboard.queries import fetch_summary
from finance_app.modules.upload.workflow import import_transactions


def credit_card_statement_type_id(conn):
    """Return the default credit card statement type ID."""
    return conn.execute(
        select(statement_types_table.c.id)
        .where(statement_types_table.c.parser_type == "credit_card")
        .order_by(statement_types_table.c.id)
        .limit(1)
    ).scalar_one()


def create_statement(conn, account_id, filename="cibc.csv"):
    """Create a statement row for import tests."""
    result = conn.execute(
        insert(statements_table).values(
            account_id=account_id,
            statement_type_id=credit_card_statement_type_id(conn),
            filename=filename,
            checksum=f"checksum-{filename}",
            extension="csv",
            raw_text="",
        )
    )
    return result.inserted_primary_key[0]


def test_credit_card_import_marks_card_and_checking_payments_as_non_reportable(app):
    """Verify card payment rows do not count as spending or income."""
    del app
    with db_core_transaction() as conn:
        checking = get_or_create_account(conn, "Main checking", account_type="checking")
        credit_card = get_or_create_account(
            conn,
            "CIBC Mastercard",
            account_type="credit_card",
            paid_from_account_name="Main checking",
        )
        conn.execute(
            insert(transactions_table).values(
                account_id=checking["id"],
                tx_date="2026-05-05",
                description="CIBC MC X6A2W2",
                amount=819.55,
                category="UNKNOWN",
                needs_review=1,
                fingerprint="checking-card-payment",
            )
        )
        statement_id = create_statement(conn, credit_card["id"])

        inserted, skipped, ignored = import_transactions(
            conn,
            statement_id,
            credit_card["id"],
            "credit_card",
            "csv",
            (
                '2026-05-08,"COSTCO WHOLESALE W527 MONTREAL, QC",277.72,,5268********2914\n'
                "2026-05-05,PAYMENT THANK YOU/PAIEMEN T MERCI,,819.55,5268********2914\n"
            ),
            undo_state={},
            import_mode="ledger",
        )

        assert (inserted, skipped, ignored) == (2, 0, 0)
        rows = conn.execute(
            select(
                transactions_table.c.description,
                transactions_table.c.amount,
                transactions_table.c.category,
                transactions_table.c.needs_review,
                transactions_table.c.transaction_kind,
            ).order_by(transactions_table.c.description)
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("CIBC MC X6A2W2", Decimal("819.55"), "Transfers", 0, "payment"),
            ("COSTCO WHOLESALE W527 MONTREAL, QC", Decimal("277.72"), "UNKNOWN", 1, "expense"),
            ("PAYMENT THANK YOU/PAIEMEN T MERCI", Decimal("-819.55"), "Transfers", 0, "payment"),
        ]

        filters = CoreFilters()
        filters.add(transactions_table.c.ignored == 0)
        summary = fetch_summary(conn, filters.criteria(), "UNKNOWN")
        assert summary["total_spending"] == Decimal("277.72")
        assert summary["total_income"] == 0
