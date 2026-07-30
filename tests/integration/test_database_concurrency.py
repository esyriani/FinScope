"""Tests for repository conflict fallbacks during race-like writes.

Exercises SQLAlchemy Core repository helpers when another writer creates the
same logical row between the helper's preselect and insert attempt.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, insert, select

from finance_app.core.constants import (
    REIMBURSEMENT_CATEGORY,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
)
from finance_app.database.tables import (
    categories as categories_table,
)
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.database.tables import (
    reimbursement_allocations as reimbursement_allocations_table,
)
from finance_app.database.tables import (
    tags as tags_table,
)
from finance_app.modules.categories import taxonomy as category_taxonomy
from finance_app.modules.merchants import repository as merchant_repository
from finance_app.modules.reimbursements import repository as reimbursement_repository
from finance_app.modules.reimbursements.service import (
    ReimbursementAllocationError,
    create_reimbursement_allocation,
)


def test_concurrent_merchant_get_or_create_reselects_existing_row(core_conn, monkeypatch):
    """Verify merchant creation tolerates a unique conflict and reselects the row."""
    real_insert_or_select = merchant_repository.insert_or_select_unique_row
    injected = {"merchant": False}

    def simulate_racing_insert(conn, insert_statement, select_statement):
        """Insert the merchant just before the repository insert is attempted."""
        if not injected["merchant"] and insert_statement.table is merchants_table:
            injected["merchant"] = True
            conn.execute(
                insert(merchants_table).values(
                    merchant_key="RACE MERCHANT",
                )
            )
        return real_insert_or_select(conn, insert_statement, select_statement)

    monkeypatch.setattr(
        merchant_repository,
        "insert_or_select_unique_row",
        simulate_racing_insert,
    )

    merchant = merchant_repository.get_or_create_merchant(
        core_conn,
        "RACE MERCHANT",
    )

    merchant_count = core_conn.execute(
        select(func.count()).select_from(merchants_table).where(merchants_table.c.merchant_key == "RACE MERCHANT")
    ).scalar_one()

    assert injected["merchant"] is True
    assert merchant_count == 1
    assert merchant["merchant_key"] == "RACE MERCHANT"


def test_concurrent_category_and_tag_metadata_create_handles_unique_conflicts(core_conn, monkeypatch):
    """Verify taxonomy metadata helpers reselect rows created by racing writers."""
    real_insert_or_select = category_taxonomy.insert_or_select_unique_row
    injected = {"category": False, "tag": False}

    def simulate_racing_insert(conn, insert_statement, select_statement):
        """Insert taxonomy rows just before each helper insert is attempted."""
        if not injected["category"] and insert_statement.table is categories_table:
            injected["category"] = True
            conn.execute(
                insert(categories_table).values(
                    name="Race Category",
                    description="Inserted by another writer",
                    instruction="",
                )
            )
        elif not injected["tag"] and insert_statement.table is tags_table:
            injected["tag"] = True
            conn.execute(
                insert(tags_table).values(
                    name="Race Tag",
                    description="Inserted by another writer",
                    instruction="",
                    color="#123456",
                )
            )
        return real_insert_or_select(conn, insert_statement, select_statement)

    monkeypatch.setattr(
        category_taxonomy,
        "insert_or_select_unique_row",
        simulate_racing_insert,
    )

    category = category_taxonomy.upsert_category_metadata(
        core_conn,
        "Race Category",
        description="Updated description",
        instruction="Updated instruction",
    )
    tag = category_taxonomy.upsert_tag_metadata(
        core_conn,
        "Race Tag",
        description="Updated tag description",
        instruction="Updated tag instruction",
        color="#abcdef",
    )

    category_row = core_conn.execute(
        select(
            categories_table.c.description,
            categories_table.c.instruction,
            func.count().over().label("row_count"),
        ).where(categories_table.c.name == "Race Category")
    ).fetchone()
    tag_row = core_conn.execute(
        select(
            tags_table.c.description,
            tags_table.c.instruction,
            tags_table.c.color,
            func.count().over().label("row_count"),
        ).where(tags_table.c.name == "Race Tag")
    ).fetchone()

    assert category == "Race Category"
    assert tag == "Race Tag"
    assert injected == {"category": True, "tag": True}
    assert tuple(category_row) == ("Updated description", "Updated instruction", 1)
    assert tuple(tag_row) == ("Updated tag description", "Updated tag instruction", "#123456", 1)


def test_concurrent_reimbursement_allocation_rechecks_reimbursement_total_after_lock(
    core_conn,
    data_factory,
    monkeypatch,
):
    """Verify racing allocations cannot overdraw the same reimbursement credit."""
    first_expense_id = data_factory.transactions.create(
        description="Race conference hotel",
        amount=Decimal("800.00"),
        category="Travel",
        transaction_kind=TRANSACTION_KIND_EXPENSE,
        needs_review=0,
        tags=["Reimbursable"],
    )
    second_expense_id = data_factory.transactions.create(
        description="Race conference meals",
        amount=Decimal("300.00"),
        category="Food",
        transaction_kind=TRANSACTION_KIND_EXPENSE,
        needs_review=0,
        tags=["Reimbursable"],
    )
    reimbursement_id = data_factory.transactions.create(
        description="Race employer reimbursement",
        amount=Decimal("-900.00"),
        category=REIMBURSEMENT_CATEGORY,
        transaction_kind=TRANSACTION_KIND_INCOME,
        needs_review=0,
    )
    real_lock_subjects = reimbursement_repository.lock_transaction_allocation_subjects
    real_sum_allocated = reimbursement_repository.sum_allocated_to_reimbursement
    injected = {"locked": False, "allocation": False}

    def lock_subjects(conn, transaction_ids):
        subjects = real_lock_subjects(conn, transaction_ids)
        injected["locked"] = True
        return subjects

    def simulate_racing_reimbursement_allocation(conn, reimbursement_transaction_id, *, exclude_allocation_id=None):
        if reimbursement_transaction_id == reimbursement_id and not injected["allocation"]:
            assert injected["locked"] is True
            injected["allocation"] = True
            reimbursement_repository.insert_allocation(
                conn,
                reimbursement_id,
                first_expense_id,
                Decimal("800.00"),
            )
        return real_sum_allocated(
            conn,
            reimbursement_transaction_id,
            exclude_allocation_id=exclude_allocation_id,
        )

    monkeypatch.setattr(reimbursement_repository, "lock_transaction_allocation_subjects", lock_subjects)
    monkeypatch.setattr(
        reimbursement_repository,
        "sum_allocated_to_reimbursement",
        simulate_racing_reimbursement_allocation,
    )

    with pytest.raises(ReimbursementAllocationError, match="still unmatched"):
        create_reimbursement_allocation(reimbursement_id, second_expense_id, Decimal("150.00"), conn=core_conn)

    allocation_count = core_conn.execute(select(func.count()).select_from(reimbursement_allocations_table)).scalar_one()

    assert injected == {"locked": True, "allocation": True}
    assert allocation_count == 0


def test_concurrent_reimbursement_allocation_rechecks_expense_total_after_lock(
    core_conn,
    data_factory,
    monkeypatch,
):
    """Verify racing allocations cannot over-reimburse the same expense."""
    expense_id = data_factory.transactions.create(
        description="Race reimbursable hotel",
        amount=Decimal("500.00"),
        category="Travel",
        transaction_kind=TRANSACTION_KIND_EXPENSE,
        needs_review=0,
        tags=["Reimbursable"],
    )
    first_reimbursement_id = data_factory.transactions.create(
        description="Race first reimbursement",
        amount=Decimal("-300.00"),
        category=REIMBURSEMENT_CATEGORY,
        transaction_kind=TRANSACTION_KIND_INCOME,
        needs_review=0,
    )
    second_reimbursement_id = data_factory.transactions.create(
        description="Race second reimbursement",
        amount=Decimal("-300.00"),
        category=REIMBURSEMENT_CATEGORY,
        transaction_kind=TRANSACTION_KIND_INCOME,
        needs_review=0,
    )
    real_lock_subjects = reimbursement_repository.lock_transaction_allocation_subjects
    real_sum_allocated = reimbursement_repository.sum_allocated_to_expense
    injected = {"locked": False, "allocation": False}

    def lock_subjects(conn, transaction_ids):
        subjects = real_lock_subjects(conn, transaction_ids)
        injected["locked"] = True
        return subjects

    def simulate_racing_expense_allocation(conn, expense_transaction_id, *, exclude_allocation_id=None):
        if expense_transaction_id == expense_id and not injected["allocation"]:
            assert injected["locked"] is True
            injected["allocation"] = True
            reimbursement_repository.insert_allocation(
                conn,
                first_reimbursement_id,
                expense_id,
                Decimal("300.00"),
            )
        return real_sum_allocated(
            conn,
            expense_transaction_id,
            exclude_allocation_id=exclude_allocation_id,
        )

    monkeypatch.setattr(reimbursement_repository, "lock_transaction_allocation_subjects", lock_subjects)
    monkeypatch.setattr(
        reimbursement_repository,
        "sum_allocated_to_expense",
        simulate_racing_expense_allocation,
    )

    with pytest.raises(ReimbursementAllocationError, match="still to reimburse"):
        create_reimbursement_allocation(second_reimbursement_id, expense_id, Decimal("250.00"), conn=core_conn)

    allocation_count = core_conn.execute(select(func.count()).select_from(reimbursement_allocations_table)).scalar_one()

    assert injected == {"locked": True, "allocation": True}
    assert allocation_count == 0
