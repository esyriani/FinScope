"""Integration tests for transaction backup CSV import previews."""

import csv
import io
import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from tests.support.database import insert_account
from tests.support.database import insert_transaction as insert_test_transaction
from tests.support.jobs import CapturingExecutor
from tests.support.transaction_backups import backup_csv, backup_row

from finance_app.background import repository as job_repository
from finance_app.background import runner
from finance_app.background.runner import JobCancelled
from finance_app.core.builtin_taxonomy import BUILTIN_CATEGORY_TAXA
from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPE_CREDIT_CARD,
    TRANSACTION_KIND_INCOME,
)
from finance_app.database.connection import init_core_db
from finance_app.database.engine import create_database_engine
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import merchants as merchants_table
from finance_app.database.tables import normalize_name_key
from finance_app.database.tables import reimbursement_allocations as allocations_table
from finance_app.database.tables import reimbursement_expense_completions as completions_table
from finance_app.database.tables import tags as tags_table
from finance_app.database.tables import transaction_tags as transaction_tags_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.reimbursements.service import create_reimbursement_allocation
from finance_app.modules.transactions import backup_workflow
from finance_app.modules.transactions.backup_import import preview_transactions_backup_import
from finance_app.modules.transactions.backup_workflow import import_transactions_backup_job
from finance_app.modules.transactions.exporter import export_transactions_backup_csv


@pytest.mark.parametrize(("expense_amount", "credit_amount"), [("70.35", "40.00"), ("117.00", "117.00")])
def test_compact_backup_rebuilds_ledger_in_a_fresh_database(core_conn, data_factory, expense_amount, credit_amount):
    """Restore usable financial state after schema initialization with no original reference records."""
    savings_id = data_factory.account(name="Cold restore savings", account_type="savings")
    card_id = data_factory.account(
        name="Cold restore card", account_type="credit_card", paid_from_account_id=savings_id
    )
    expense_id = data_factory.transaction(
        account_id=card_id,
        description="Cold restore expense",
        amount=Decimal(expense_amount),
        category="Cold restore category",
        category_source="manual",
        category_confidence=1.0,
        needs_review=0,
        category_metadata=json.dumps({"synthetic_note": "x" * 140000}),
        merchant_from_description=True,
        tags=["Reimbursable", "Family; rides, club"],
        fingerprint="cold-expense",
    )
    credit_id = data_factory.transaction(
        description="Cold restore credit",
        amount=-Decimal(credit_amount),
        category="Reimbursement",
        category_source="ai",
        category_confidence=0.89,
        needs_review=1,
        transaction_kind="income",
        fingerprint="cold-credit",
    )
    create_reimbursement_allocation(credit_id, expense_id, Decimal(credit_amount), conn=core_conn)
    original_created_at = "2001-01-01T09:00:00Z"
    core_conn.execute(update(transactions_table).values(created_at=original_created_at))
    core_conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == expense_id)
        .values(ignored=1, categorized_at="2026-05-04T12:00:00Z", reviewed_at="2026-05-04T13:00:00Z")
    )
    core_conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == credit_id)
        .values(categorized_at="2026-05-05T12:00:00Z")
    )
    core_conn.execute(update(allocations_table).values(created_at=original_created_at, updated_at=original_created_at))
    core_conn.execute(insert(completions_table).values(expense_transaction_id=expense_id))
    csv_text = export_transactions_backup_csv(core_conn)

    destination = create_database_engine("sqlite:///:memory:")
    try:
        init_core_db(destination)
        with destination.begin() as conn:
            preview = preview_transactions_backup_import(conn, csv_text)
            backup_workflow.validate_backup_preview(preview)
            result = backup_workflow.restore_transactions_backup(conn, preview, "skip_duplicates")

            assert result.imported == 2
            assert result.allocations == 1
            restored = {row["fingerprint"]: row for row in conn.execute(select(transactions_table)).mappings()}
            expense = restored["cold-expense"]
            assert expense["amount"] == Decimal(expense_amount)
            assert expense["category"] == "Cold restore category"
            assert (expense["category_source"], expense["needs_review"], expense["ignored"]) == ("manual", 0, 1)
            assert expense["category_metadata"] is None
            assert expense["category_confidence"] == 1.0
            assert expense["categorized_at"] == "2026-05-04T12:00:00Z"
            assert expense["reviewed_at"] == "2026-05-04T13:00:00Z"
            credit = restored["cold-credit"]
            assert credit["amount"] == -Decimal(credit_amount)
            assert (credit["category_source"], credit["needs_review"], credit["category_confidence"]) == ("ai", 1, 0.89)
            assert credit["categorized_at"] == "2026-05-05T12:00:00Z"
            assert credit["reviewed_at"] is None
            assert (
                conn.execute(
                    select(categories_table.c.builtin_key).where(categories_table.c.id == credit["category_id"])
                ).scalar_one()
                == "reimbursement"
            )
            assert all(row["created_at"] and row["created_at"] != original_created_at for row in restored.values())
            restored_accounts = {row["name"]: row for row in conn.execute(select(accounts_table)).mappings()}
            card = restored_accounts["Cold restore card"]
            savings = restored_accounts["Cold restore savings"]
            assert (card["account_type"], savings["account_type"]) == ("credit_card", "savings")
            assert card["paid_from_account_id"] == savings["id"]
            assert expense["account_id"] == card["id"]
            names = (
                conn.execute(
                    select(tags_table.c.name)
                    .join(transaction_tags_table, transaction_tags_table.c.tag_id == tags_table.c.id)
                    .where(transaction_tags_table.c.transaction_id == expense["id"])
                )
                .scalars()
                .all()
            )
            assert set(names) == {"Reimbursable", "Family; rides, club"}
            link = conn.execute(select(allocations_table)).mappings().one()
            assert link["reimbursement_transaction_id"] == restored["cold-credit"]["id"]
            assert link["expense_transaction_id"] == expense["id"]
            assert link["amount"] == Decimal(credit_amount)
            assert link["created_at"] and link["created_at"] != original_created_at
            assert link["updated_at"] and link["updated_at"] != original_created_at
            assert conn.execute(select(completions_table.c.expense_transaction_id)).scalar_one() == expense["id"]
    finally:
        destination.dispose()


def test_backup_import_resolves_builtin_categories_by_normalized_name(core_conn):
    """One mixed backup resolves every built-in name to its initialized identity."""
    expected = {
        f"builtin-{builtin.key}": (
            core_conn.execute(
                select(categories_table.c.id).where(categories_table.c.builtin_key == builtin.key)
            ).scalar_one(),
            builtin.name,
        )
        for builtin in BUILTIN_CATEGORY_TAXA
    }
    csv_text = backup_csv(
        [
            backup_row(
                description=f"Restore {builtin.name}",
                category=f"  {builtin.name.swapcase()}  ",
                fingerprint=f"builtin-{builtin.key}",
            )
            for builtin in BUILTIN_CATEGORY_TAXA
        ]
    )
    core_conn.commit()

    import_transactions_backup_job(csv_text, "skip_duplicates")

    restored = core_conn.execute(select(transactions_table)).mappings().all()
    assert {row["fingerprint"]: (row["category_id"], row["category"]) for row in restored} == expected


@pytest.mark.parametrize("state", ["missing", "unmarked", "incorrect"])
def test_backup_import_rejects_uninitialized_builtin_categories(core_conn, state):
    """Missing or corrupted built-ins must not be recreated as ordinary user categories."""
    condition = categories_table.c.builtin_key == "reimbursement"
    if state == "missing":
        core_conn.execute(delete(categories_table).where(condition))
    else:
        core_conn.execute(
            update(categories_table).where(condition).values(builtin_key=None if state == "unmarked" else "incorrect")
        )
    core_conn.commit()
    csv_text = backup_csv(
        [
            backup_row(
                account_name="Uninitialized restore",
                category="Reimbursement",
                amount="'-117.00",
                transaction_kind="income",
            )
        ]
    )

    with pytest.raises(ValueError, match="built-in category is missing or incorrectly configured"):
        import_transactions_backup_job(csv_text, "skip_duplicates")

    assert core_conn.execute(select(func.count()).select_from(transactions_table)).scalar_one() == 0
    assert (
        core_conn.execute(select(accounts_table.c.id).where(accounts_table.c.name == "Uninitialized restore")).first()
        is None
    )
    category = (
        core_conn.execute(select(categories_table).where(categories_table.c.name == "Reimbursement")).mappings().first()
    )
    if state == "missing":
        assert category is None
    else:
        assert category is not None
        assert category["builtin_key"] == (None if state == "unmarked" else "incorrect")


def test_transaction_backup_preview_parses_exported_rows(core_conn):
    """Verify exported backups parse back to typed rows and existing references."""
    paid_from_id = insert_account(core_conn, name="Main Checking")
    account_id = insert_account(
        core_conn,
        name="Rewards Visa",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=paid_from_id,
    )
    tx_id = insert_test_transaction(
        core_conn,
        description="=Refund credit",
        amount=-42.50,
        category="UNKNOWN",
        category_source="manual",
        category_confidence=1.0,
        needs_review=0,
        ignored=1,
        transaction_kind=TRANSACTION_KIND_INCOME,
        account_id=account_id,
        merchant_from_description=True,
        tags=["Tax"],
        tag_source="manual",
        fingerprint="backup-preview-roundtrip",
    )

    preview = preview_transactions_backup_import(core_conn, export_transactions_backup_csv(core_conn))

    assert preview.total_rows == 1
    assert preview.valid_row_count == 1
    assert preview.row_errors == ()
    transaction = preview.transactions[0]
    assert transaction["description"] == "=Refund credit"
    assert transaction["amount"] == Decimal("-42.50")
    assert transaction["tx_date"] == date(2026, 1, 2)
    assert transaction["tags"] == ("Tax",)

    accounts_by_name = {account.name: account for account in preview.accounts}
    assert accounts_by_name["Rewards Visa"].action == "existing"
    assert accounts_by_name["Main Checking"].action == "existing"
    assert preview.categories[0].name == "UNKNOWN"
    assert preview.categories[0].action == "existing"
    assert preview.tags[0].name == "Tax"
    assert preview.tags[0].action == "existing"
    assert preview.merchants[0].name == normalize_merchant("=Refund credit").merchant_key
    assert preview.merchants[0].action == "existing"
    assert preview.duplicates[0].match_type == "fingerprint"
    assert preview.duplicates[0].existing_transaction_id == tx_id


def test_transaction_backup_preview_reports_reference_changes_without_writes(core_conn):
    """Verify preview plans account updates and missing references without mutating rows."""
    existing_account_id = insert_account(
        core_conn,
        name="Backup Visa",
        account_type=ACCOUNT_TYPE_CHECKING,
    )
    category_name = "Backup Import Category"
    tag_name = "Backup Import Tag"
    merchant_name = "BACKUP IMPORT MERCHANT"
    paid_from_name = "Main Backup Checking"

    preview = preview_transactions_backup_import(
        core_conn,
        backup_csv(
            [
                backup_row(
                    account_name="Backup Visa",
                    account_type=ACCOUNT_TYPE_CREDIT_CARD,
                    paid_from_account_name=paid_from_name,
                    merchant_name=merchant_name,
                    category=category_name,
                    tags=json.dumps([tag_name]),
                    fingerprint="backup-preview-reference-changes",
                )
            ]
        ),
    )

    accounts_by_name = {account.name: account for account in preview.accounts}
    assert accounts_by_name["Backup Visa"].action == "update"
    assert accounts_by_name["Backup Visa"].existing_id == existing_account_id
    assert accounts_by_name["Backup Visa"].changes == ("account_type", "paid_from_account")
    assert accounts_by_name[paid_from_name].action == "create"
    assert [(item.name, item.action, item.existing_id) for item in preview.categories] == [
        (category_name, "create", None)
    ]
    assert [(item.name, item.action, item.existing_id) for item in preview.tags] == [(tag_name, "create", None)]
    assert [(item.name, item.action, item.existing_id) for item in preview.merchants] == [
        (merchant_name, "create", None)
    ]
    assert preview.transactions[0]["tags"] == (tag_name,)

    existing_account = (
        core_conn.execute(
            select(accounts_table.c.account_type, accounts_table.c.paid_from_account_id).where(
                accounts_table.c.id == existing_account_id
            )
        )
        .mappings()
        .one()
    )
    assert existing_account["account_type"] == ACCOUNT_TYPE_CHECKING
    assert existing_account["paid_from_account_id"] is None
    assert not core_conn.execute(
        select(accounts_table.c.id).where(accounts_table.c.name_key == normalize_name_key(paid_from_name))
    ).fetchone()
    assert not core_conn.execute(
        select(categories_table.c.id).where(categories_table.c.name_key == normalize_name_key(category_name))
    ).fetchone()
    assert not core_conn.execute(
        select(tags_table.c.id).where(tags_table.c.name_key == normalize_name_key(tag_name))
    ).fetchone()
    assert not core_conn.execute(
        select(merchants_table.c.id).where(merchants_table.c.merchant_key == merchant_name)
    ).fetchone()


def test_transaction_backup_preview_flags_detail_duplicates(core_conn):
    """Verify duplicate detection catches rows whose fingerprint changed."""
    account_id = insert_account(core_conn, name="Duplicate Checking")
    existing_id = insert_test_transaction(
        core_conn,
        description="Duplicate Coffee",
        amount=8.75,
        tx_date="2026-05-05",
        account_id=account_id,
        fingerprint="backup-preview-existing-detail",
    )

    preview = preview_transactions_backup_import(
        core_conn,
        backup_csv(
            [
                backup_row(
                    account_name="Duplicate Checking",
                    account_type=ACCOUNT_TYPE_CHECKING,
                    tx_date="2026-05-05",
                    description="Duplicate Coffee",
                    amount="8.75",
                    fingerprint="backup-preview-new-detail",
                )
            ]
        ),
    )

    assert preview.duplicate_count == 1
    assert preview.importable_without_duplicates_count == 0
    assert preview.duplicates[0].match_type == "details"
    assert preview.duplicates[0].reason_label == "Same date, account, description, and amount"
    assert preview.duplicates[0].existing_transaction_id == existing_id


def test_transaction_backup_preview_collects_row_errors_and_valid_rows(core_conn):
    """Preview valid rows but reject the whole restore when any row is invalid."""
    csv_text = backup_csv(
        [
            backup_row(tx_date="not-a-date", fingerprint="backup-preview-bad"),
            backup_row(fingerprint="backup-preview-good"),
        ]
    )
    tables = (accounts_table, categories_table, merchants_table, tags_table, transactions_table)
    before = {table.name: core_conn.execute(select(table)).mappings().all() for table in tables}
    preview = preview_transactions_backup_import(core_conn, csv_text)

    assert preview.total_rows == 2
    assert preview.valid_row_count == 1
    assert preview.transactions[0]["fingerprint"] == "backup-preview-good"
    assert preview.row_errors == ("Row 2: tx_date must be an ISO date.",)
    core_conn.commit()
    with pytest.raises(ValueError, match="invalid rows"):
        import_transactions_backup_job(csv_text, "import_all")
    assert {table.name: core_conn.execute(select(table)).mappings().all() for table in tables} == before


@pytest.mark.parametrize(("action", "imported"), [("skip_duplicates", 2), ("import_all", 3)])
def test_backup_file_duplicates_agree_with_preview_for_new_accounts(core_conn, action, imported):
    """Preview and restore share duplicate decisions before any accounts exist."""
    repeated = backup_row(account_name="File checking", fingerprint="file-repeated")
    other_account = backup_row(account_name="Other file checking", fingerprint="file-distinct")
    csv_text = backup_csv([repeated, repeated, other_account])

    preview = preview_transactions_backup_import(core_conn, csv_text)
    assert preview.duplicate_count == 1
    assert preview.importable_without_duplicates_count == 2
    assert preview.duplicates[0].existing_transaction_id is None
    assert preview.duplicates[0].duplicate_of_line_number == 2
    assert preview.duplicates[0].reason_label == "Repeated transaction in this backup"

    summary = import_transactions_backup_job(csv_text, action)
    assert f"{imported} imported, {3 - imported} duplicates skipped" in summary
    assert core_conn.execute(select(func.count()).select_from(transactions_table)).scalar_one() == imported


def test_backup_import_restores_details_and_account_metadata(core_conn, data_factory):
    """Restore ledger state and create references without database IDs or detailed audit history."""
    account_id = data_factory.account(name="Restore card", account_type="checking")
    csv_text = backup_csv(
        [
            backup_row(
                account_name="Restore card",
                account_type="credit_card",
                paid_from_account_name="Restore checking",
                merchant_name="RESTORE SHOP",
                amount="'-23.45",
                description="'=Credit <script>",
                category="Restore category",
                category_source="manual",
                category_confidence="0.9",
                needs_review="1",
                ignored="1",
                transaction_kind="refund",
                fingerprint="restore-detailed",
                categorized_at="2026-05-04T12:00:00Z",
                reviewed_at="2026-05-04T13:00:00Z",
                tags=json.dumps(["Restore, tag"]),
            )
        ]
    )

    result = import_transactions_backup_job(csv_text, "import_all")

    row = (
        core_conn.execute(select(transactions_table).where(transactions_table.c.fingerprint == "restore-detailed"))
        .mappings()
        .one()
    )
    assert "1 imported" in result
    assert row["account_id"] == account_id
    assert row["statement_id"] is None
    assert row["category_rule_id"] is None
    assert row["description"] == "=Credit <script>"
    assert row["amount"] == Decimal("-23.45")
    assert row["tx_date"] == "2026-05-03"
    assert row["created_at"] is not None
    assert row["categorized_at"] == "2026-05-04T12:00:00Z"
    assert row["reviewed_at"] == "2026-05-04T13:00:00Z"
    assert row["category_metadata"] is None
    assert (row["needs_review"], row["ignored"], row["category_source"], row["transaction_kind"]) == (
        1,
        1,
        "manual",
        "refund",
    )
    tag = (
        core_conn.execute(select(transaction_tags_table).where(transaction_tags_table.c.transaction_id == row["id"]))
        .mappings()
        .one()
    )
    assert tag["source"] == "unknown"
    assert tag["rule_id"] is None
    assert tag["assigned_at"] is not None
    assert (
        core_conn.execute(select(tags_table.c.name).where(tags_table.c.id == tag["tag_id"])).scalar_one()
        == "Restore, tag"
    )
    account = core_conn.execute(select(accounts_table).where(accounts_table.c.id == account_id)).mappings().one()
    assert account["account_type"] == "credit_card"
    assert (
        core_conn.execute(
            select(accounts_table.c.name).where(accounts_table.c.id == account["paid_from_account_id"])
        ).scalar_one()
        == "Restore checking"
    )
    assert (
        core_conn.execute(
            select(categories_table.c.name).where(categories_table.c.id == row["category_id"])
        ).scalar_one()
        == "Restore category"
    )
    assert (
        core_conn.execute(
            select(merchants_table.c.merchant_key).where(merchants_table.c.id == row["merchant_id"])
        ).scalar_one()
        == "RESTORE SHOP"
    )


@pytest.mark.parametrize(
    ("action", "expected_imported", "expected_skipped"), [("skip_duplicates", 1, 2), ("import_all", 3, 0)]
)
def test_backup_import_honors_duplicate_choices_at_execution(core_conn, action, expected_imported, expected_skipped):
    """Recheck both duplicate identities at execution and retain full copies when requested."""
    existing_id = insert_test_transaction(
        core_conn, description="Existing backup row", amount=12.34, tx_date="2026-05-03", fingerprint="restore-existing"
    )
    csv_text = backup_csv(
        [
            backup_row(description="Existing backup row", fingerprint="restore-existing"),
            backup_row(description="Existing backup row", fingerprint="restore-new-fingerprint"),
            backup_row(description="Fresh backup row", fingerprint="restore-fresh"),
        ]
    )

    summary = import_transactions_backup_job(csv_text, action)

    rows = core_conn.execute(select(transactions_table)).mappings().all()
    assert len(rows) == 1 + expected_imported
    assert len({row["fingerprint"] for row in rows}) == len(rows)
    assert f"{expected_imported} imported, {expected_skipped} duplicates skipped" in summary
    assert (
        core_conn.execute(
            select(transactions_table.c.needs_review).where(transactions_table.c.id == existing_id)
        ).scalar_one()
        == 1
    )


def test_backup_restore_counts_duplicates_inserted_after_preview(core_conn):
    """A uniqueness conflict after preview must count a skip and preserve the existing row."""
    csv_text = backup_csv([backup_row(category="Food", tags='["Do not restore"]', fingerprint="restore-after-preview")])
    preview = preview_transactions_backup_import(core_conn, csv_text)
    assert not preview.duplicates
    insert_test_transaction(
        core_conn,
        fingerprint="restore-after-preview",
        description="Keep concurrent transaction",
        tags=["Keep existing tag"],
    )
    before = export_transactions_backup_csv(core_conn)

    result = backup_workflow.restore_transactions_backup(core_conn, preview, "skip_duplicates")

    assert (result.imported, result.skipped, result.allocations) == (0, 1, 0)
    assert export_transactions_backup_csv(core_conn) == before


@pytest.mark.parametrize("keep_credit", [False, True])
def test_backup_import_restores_reimbursement_links_once(core_conn, keep_credit):
    """Restore one portable link per allocation, including a retained duplicate credit."""
    expense_id = insert_test_transaction(
        core_conn,
        description="Restored reimbursable expense",
        amount=50,
        tags=["Reimbursable"],
        fingerprint="restore-expense",
    )
    credit_id = insert_test_transaction(
        core_conn,
        description="Restored reimbursement",
        amount=-40,
        category="Reimbursement",
        transaction_kind="income",
        fingerprint="restore-credit",
    )
    create_reimbursement_allocation(credit_id, expense_id, Decimal("40"), conn=core_conn)
    core_conn.execute(insert(completions_table).values(expense_transaction_id=expense_id))
    csv_text = export_transactions_backup_csv(core_conn)
    exported_rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert sum(bool(row["reimbursement_allocations"]) for row in exported_rows) == 1
    exported_link = json.loads(
        next(row["reimbursement_allocations"] for row in exported_rows if row["reimbursement_allocations"])
    )[0]
    assert exported_link["expense_fingerprint"] == "restore-expense"
    assert set(exported_link) == {"expense_fingerprint", "amount"}
    core_conn.execute(
        delete(transactions_table).where(
            transactions_table.c.id.in_([expense_id] if keep_credit else [expense_id, credit_id])
        )
    )
    core_conn.commit()

    summary = import_transactions_backup_job(csv_text, "skip_duplicates")

    restored = {row["fingerprint"]: row["id"] for row in core_conn.execute(select(transactions_table)).mappings()}
    assert "1 reimbursement links restored" in summary, (summary, restored, exported_rows)
    link = core_conn.execute(select(allocations_table)).mappings().one()
    assert link["reimbursement_transaction_id"] == restored["restore-credit"]
    assert link["expense_transaction_id"] == restored["restore-expense"]
    assert link["amount"] == Decimal("40.00")
    assert "1 reimbursement links restored" in summary
    assert (
        core_conn.execute(select(completions_table.c.expense_transaction_id)).scalar_one()
        == restored["restore-expense"]
    )
    if keep_credit:
        assert restored["restore-credit"] == credit_id


@pytest.mark.parametrize(
    ("expense_amounts", "credit_amounts", "links"),
    [
        (("70.35", "66.65"), ("117.00",), ((0, 0, "70.35"), (0, 1, "46.65"))),
        (("150.00",), ("40.00", "77.00"), ((0, 0, "40.00"), (1, 0, "77.00"))),
    ],
    ids=["credit-split-across-expenses", "expense-covered-by-multiple-credits"],
)
def test_backup_import_restores_split_reimbursements(core_conn, data_factory, expense_amounts, credit_amounts, links):
    """Minimal link records preserve exact partial allocations in both split directions."""
    expenses = [
        data_factory.transaction(
            description=f"Split expense {index}",
            amount=Decimal(amount),
            tags=["Reimbursable"],
            fingerprint=f"split-expense-{index}",
        )
        for index, amount in enumerate(expense_amounts)
    ]
    credits = [
        data_factory.transaction(
            description=f"Split credit {index}",
            amount=-Decimal(amount),
            category="Reimbursement",
            transaction_kind="income",
            fingerprint=f"split-credit-{index}",
        )
        for index, amount in enumerate(credit_amounts)
    ]
    for credit, expense, amount in links:
        create_reimbursement_allocation(credits[credit], expenses[expense], Decimal(amount), conn=core_conn)
    csv_text = export_transactions_backup_csv(core_conn)
    core_conn.execute(delete(transactions_table))
    core_conn.commit()

    result = import_transactions_backup_job(csv_text, "skip_duplicates")

    restored = {row["fingerprint"]: row["id"] for row in core_conn.execute(select(transactions_table)).mappings()}
    assert f"{len(expenses) + len(credits)} imported" in result
    assert f"{len(links)} reimbursement links restored" in result
    assert {
        (row["reimbursement_transaction_id"], row["expense_transaction_id"], row["amount"])
        for row in core_conn.execute(select(allocations_table)).mappings()
    } == {
        (restored[f"split-credit-{credit}"], restored[f"split-expense-{expense}"], Decimal(amount))
        for credit, expense, amount in links
    }


def test_backup_import_rolls_back_rows_references_and_account_updates(core_conn):
    """A late allocation validation failure rolls back the entire restore."""
    account_id = insert_account(core_conn, name="Rollback card")
    link = {"expense_fingerprint": "rollback-expense", "amount": "60.00"}
    csv_text = backup_csv(
        [
            backup_row(
                account_name="Rollback card",
                account_type="credit_card",
                paid_from_account_name="Rollback funding",
                category="Rollback category",
                tags='["Rollback tag"]',
                amount="50.00",
                fingerprint="rollback-expense",
            ),
            backup_row(
                description="Rollback credit",
                amount="'-40.00",
                category="Reimbursement",
                transaction_kind="income",
                fingerprint="rollback-credit",
                reimbursement_allocations=json.dumps([link]),
            ),
        ]
    )

    with pytest.raises(ValueError, match="exceed"):
        import_transactions_backup_job(csv_text, "import_all")

    assert core_conn.execute(select(func.count()).select_from(transactions_table)).scalar_one() == 0
    account = core_conn.execute(select(accounts_table).where(accounts_table.c.id == account_id)).mappings().one()
    assert account["account_type"] == "checking"
    assert account["paid_from_account_id"] is None
    assert (
        core_conn.execute(select(accounts_table.c.id).where(accounts_table.c.name == "Rollback funding")).first()
        is None
    )
    assert (
        core_conn.execute(select(categories_table.c.id).where(categories_table.c.name == "Rollback category")).first()
        is None
    )
    assert core_conn.execute(select(tags_table.c.id).where(tags_table.c.name == "Rollback tag")).first() is None


def test_backup_database_failure_rolls_back_and_sanitizes_persisted_history(core_conn, data_factory, monkeypatch):
    """A database failure after ledger/tag writes exposes no uploaded values in job history."""
    account_id = data_factory.account(name="Existing backup account")
    original_restore_tags = backup_workflow.repository.restore_backup_tags

    def fail_after_tag_write(conn, transaction_id, tags):
        """Simulate a database failure after proving financial rows were written."""
        original_restore_tags(conn, transaction_id, tags)
        assert conn.execute(select(transactions_table.c.id)).scalar_one() == transaction_id
        assert conn.execute(select(tags_table.c.id).where(tags_table.c.name == "PRIVATE_TAG")).first()
        raise SQLAlchemyError("INSERT PRIVATE_DESCRIPTION 123.45 PRIVATE_TAG")

    monkeypatch.setattr(backup_workflow.repository, "restore_backup_tags", fail_after_tag_write)
    monkeypatch.setattr(runner, "_executor", CapturingExecutor())
    monkeypatch.setattr(runner, "_jobs", {})
    monkeypatch.setattr(runner, "_job_history_enabled", True)
    csv_text = backup_csv(
        [
            backup_row(
                account_name="Existing backup account",
                account_type="credit_card",
                paid_from_account_name="New backup funding",
                description="PRIVATE_DESCRIPTION",
                amount="123.45",
                category="New backup category",
                tags='["PRIVATE_TAG"]',
            )
        ]
    )
    job_id = runner.submit_background_job(
        "Import transaction backup", import_transactions_backup_job, csv_text, "import_all"
    )

    runner.run_job(job_id, import_transactions_backup_job, (csv_text, "import_all"), {})

    job = job_repository.get_job(core_conn, job_id)
    assert job["status"] == "failed"
    assert job["error"] == "ValueError: Transaction backup import failed. No changes were saved."
    assert all(value not in json.dumps(job) for value in ("PRIVATE_DESCRIPTION", "123.45", "PRIVATE_TAG"))
    assert core_conn.execute(select(func.count()).select_from(transactions_table)).scalar_one() == 0
    account = core_conn.execute(select(accounts_table).where(accounts_table.c.id == account_id)).mappings().one()
    assert account["account_type"] == "checking"
    assert account["paid_from_account_id"] is None
    assert core_conn.execute(select(func.count()).select_from(accounts_table)).scalar_one() == 1
    assert (
        core_conn.execute(select(categories_table.c.id).where(categories_table.c.name == "New backup category")).first()
        is None
    )
    assert core_conn.execute(select(tags_table.c.id).where(tags_table.c.name == "PRIVATE_TAG")).first() is None


@pytest.mark.parametrize(
    ("scenario", "message"),
    [("missing", "transaction missing"), ("conflicting", "conflicting reimbursement"), ("self", "matched to itself")],
)
def test_backup_rejects_invalid_reimbursement_relationships_atomically(core_conn, scenario, message):
    """Reach relationship validation through valid CSV, without fabricating internal state."""
    fingerprint = {"missing": "missing-expense", "conflicting": "expense", "self": "credit"}[scenario]
    links = [{"expense_fingerprint": fingerprint, "amount": "10.00"}]
    if scenario == "conflicting":
        links.append({"expense_fingerprint": fingerprint, "amount": "11.00"})
    csv_text = backup_csv(
        [
            backup_row(
                description="Expense", amount="50.00", category="Food", tags='["Reimbursable"]', fingerprint="expense"
            ),
            backup_row(
                description="Credit",
                amount="'-40.00",
                category="Reimbursement",
                transaction_kind="income",
                fingerprint="credit",
                reimbursement_allocations=json.dumps(links),
            ),
        ]
    )

    with pytest.raises(ValueError, match=message):
        import_transactions_backup_job(csv_text, "import_all")

    assert core_conn.execute(select(func.count()).select_from(transactions_table)).scalar_one() == 0
    assert core_conn.execute(select(func.count()).select_from(allocations_table)).scalar_one() == 0


def test_backup_links_existing_expense_outside_csv_and_preserves_skipped_links(core_conn, data_factory):
    """Restore a partial backup against an existing expense and leave its link untouched on retry."""
    expense_id = data_factory.transaction(amount="50.00", tags=["Reimbursable"], fingerprint="existing-expense")
    csv_text = backup_csv(
        [
            backup_row(
                description="Credit only",
                amount="'-40.00",
                category="Reimbursement",
                transaction_kind="income",
                fingerprint="partial-credit",
                reimbursement_allocations=json.dumps([{"expense_fingerprint": "existing-expense", "amount": "40.00"}]),
            ),
        ]
    )

    assert "1 imported" in import_transactions_backup_job(csv_text, "skip_duplicates")
    link = core_conn.execute(select(allocations_table)).mappings().one()
    assert link["expense_transaction_id"] == expense_id
    assert link["amount"] == Decimal("40.00")
    assert "0 imported, 1 duplicates skipped, 0 reimbursement links restored" in import_transactions_backup_job(
        csv_text, "skip_duplicates"
    )
    assert core_conn.execute(select(allocations_table)).mappings().all() == [link]


def test_backup_import_cancellation_rolls_back_in_progress_rows(core_conn, monkeypatch):
    """Cooperative cancellation after the first insert rolls back the whole backup."""
    checks = iter([False, False, True])

    def cancel_after_first_row(message):
        """Signal cancellation when the second row is about to be restored."""
        if next(checks):
            raise JobCancelled(message)

    monkeypatch.setattr(backup_workflow, "raise_if_cancel_requested", cancel_after_first_row)
    csv_text = backup_csv(
        [
            backup_row(
                account_name="Cancelled account", description="First cancelled row", fingerprint="cancelled-first"
            ),
            backup_row(description="Second cancelled row", fingerprint="cancelled-second"),
        ]
    )
    with pytest.raises(JobCancelled, match="No changes were saved"):
        import_transactions_backup_job(csv_text, "import_all")
    assert core_conn.execute(select(func.count()).select_from(transactions_table)).scalar_one() == 0
    assert (
        core_conn.execute(select(accounts_table.c.id).where(accounts_table.c.name == "Cancelled account")).first()
        is None
    )


def test_backup_import_enforces_builtin_tag_eligibility(core_conn):
    """A malformed backup cannot attach Reimbursable to an incoming credit."""
    csv_text = backup_csv([backup_row(amount="'-10.00", transaction_kind="income", tags='["Reimbursable"]')])
    with pytest.raises(ValueError, match="tag assignments"):
        import_transactions_backup_job(csv_text, "import_all")
    assert core_conn.execute(select(func.count()).select_from(transactions_table)).scalar_one() == 0


@pytest.mark.parametrize("include_funding_row", [False, True])
def test_backup_import_preserves_authoritative_funding_account_type(core_conn, include_funding_row):
    """A card funding link must not override the funded account's own backup metadata."""
    root_id = insert_account(core_conn, name="Root checking")
    savings_id = insert_account(core_conn, name="Funding savings", account_type="savings", paid_from_account_id=root_id)
    card_id = insert_account(
        core_conn, name="Savings-funded card", account_type="credit_card", paid_from_account_id=savings_id
    )
    insert_test_transaction(core_conn, account_id=card_id, fingerprint="savings-funded-card")
    if include_funding_row:
        insert_test_transaction(
            core_conn, description="Savings activity", account_id=savings_id, fingerprint="funding-savings"
        )
    csv_text = export_transactions_backup_csv(core_conn)
    core_conn.commit()

    summary = import_transactions_backup_job(csv_text, "import_all")

    assert f"{2 if include_funding_row else 1} imported" in summary
    assert (
        core_conn.execute(select(accounts_table.c.account_type).where(accounts_table.c.id == savings_id)).scalar_one()
        == "savings"
    )
    assert (
        core_conn.execute(
            select(accounts_table.c.paid_from_account_id).where(accounts_table.c.id == card_id)
        ).scalar_one()
        == savings_id
    )
    assert (
        core_conn.execute(
            select(accounts_table.c.paid_from_account_id).where(accounts_table.c.id == savings_id)
        ).scalar_one()
        == root_id
    )


@pytest.mark.parametrize("existing_funding", [False, True])
def test_backup_import_restores_funding_type_without_funding_transactions(core_conn, existing_funding):
    """Restore exported savings metadata and retain destination-only funding links."""
    savings_id = insert_account(core_conn, name="Backup-only savings", account_type="savings")
    card_id = insert_account(
        core_conn, name="Backup-only card", account_type="credit_card", paid_from_account_id=savings_id
    )
    transaction_id = insert_test_transaction(core_conn, account_id=card_id, fingerprint="backup-only-card")
    csv_text = export_transactions_backup_csv(core_conn)
    core_conn.execute(delete(transactions_table).where(transactions_table.c.id == transaction_id))
    core_conn.execute(delete(accounts_table).where(accounts_table.c.id == card_id))
    core_conn.execute(delete(accounts_table).where(accounts_table.c.id == savings_id))
    core_conn.commit()
    root_id = None
    if existing_funding:
        root_id = insert_account(core_conn, name="Destination funding")
        insert_account(core_conn, name="Backup-only savings", account_type="checking", paid_from_account_id=root_id)

    summary = import_transactions_backup_job(csv_text, "skip_duplicates")

    assert "1 imported, 0 duplicates skipped" in summary
    funding = (
        core_conn.execute(select(accounts_table).where(accounts_table.c.name == "Backup-only savings")).mappings().one()
    )
    assert funding["account_type"] == "savings"
    assert funding["paid_from_account_id"] == root_id
    restored_card = (
        core_conn.execute(select(accounts_table).where(accounts_table.c.name == "Backup-only card")).mappings().one()
    )
    assert restored_card["paid_from_account_id"] == funding["id"]


def test_backup_preview_rejects_conflicting_funding_account_types(core_conn):
    """Repeated account metadata must agree even when referenced only as funding."""
    csv_text = backup_csv(
        [
            backup_row(
                account_name="First card",
                account_type="credit_card",
                paid_from_account_name="Shared funding",
                paid_from_account_type="savings",
                fingerprint="first-card",
            ),
            backup_row(
                account_name="Second card",
                account_type="credit_card",
                paid_from_account_name="Shared funding",
                paid_from_account_type="checking",
                fingerprint="second-card",
            ),
        ]
    )

    preview = preview_transactions_backup_import(core_conn, csv_text)

    assert preview.row_errors == ('Account "Shared funding" has conflicting metadata in the backup CSV.',)
    core_conn.commit()
    with pytest.raises(ValueError, match="invalid rows or conflicting details"):
        import_transactions_backup_job(csv_text, "import_all")
