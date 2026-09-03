"""Integration tests for reimbursement allocation services."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from finance_app.core.constants import (
    REIMBURSEMENT_CATEGORY,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
)
from finance_app.core.money import money_to_decimal
from finance_app.core.reporting import spending_impact_amount_expression
from finance_app.database.tables import reimbursement_allocations as reimbursement_allocations_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.reimbursements import presenter, queries
from finance_app.modules.reimbursements.service import (
    ReimbursementAllocationError,
    complete_reimbursable_expense,
    create_reimbursement_allocation,
    delete_reimbursement_allocation,
    resume_reimbursable_expense,
    update_reimbursement_allocation_amount,
)


def expense_transaction(
    data_factory,
    *,
    amount="1000.00",
    category="Travel",
    transaction_kind=TRANSACTION_KIND_EXPENSE,
    tags=("Conference", "Reimbursable"),
):
    """Create a positive expense transaction for reimbursement tests."""
    return data_factory.transactions.create(
        description="Conference expense",
        amount=Decimal(amount),
        category=category,
        transaction_kind=transaction_kind,
        needs_review=0,
        tags=list(tags),
    )


def reimbursement_transaction(data_factory, *, amount="-900.00", category=REIMBURSEMENT_CATEGORY):
    """Create an incoming reimbursement credit for reimbursement tests."""
    return data_factory.transactions.create(
        description="Employer reimbursement",
        amount=Decimal(amount),
        category=category,
        transaction_kind=TRANSACTION_KIND_INCOME,
        needs_review=0,
    )


def allocation_count(conn):
    """Return the number of persisted reimbursement allocations."""
    return conn.execute(select(func.count()).select_from(reimbursement_allocations_table)).scalar_one()


def spending_impact_for(conn, transaction_id: int) -> Decimal:
    """Return shared reporting spending impact for one transaction."""
    amount = conn.execute(
        select(spending_impact_amount_expression())
        .select_from(transactions_table)
        .where(transactions_table.c.id == transaction_id)
    ).scalar_one()
    return money_to_decimal(amount)


def set_ignored(conn, transaction_id: int) -> None:
    """Mark a test transaction ignored."""
    conn.execute(update(transactions_table).where(transactions_table.c.id == transaction_id).values(ignored=1))
    conn.commit()


def set_category(conn, transaction_id: int, category: str) -> None:
    """Move a test transaction to another category."""
    conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
        .values(category=category, category_id=resolve_category_id(conn, category))
    )
    conn.commit()


def test_reimbursement_allocation_tracks_partial_balances(core_conn, data_factory):
    """Verify a reimbursement can partially offset a larger expense."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")

    result = create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("900.00"), conn=core_conn)

    assert result.reimbursement_transaction_id == reimbursement_id
    assert result.expense_transaction_id == expense_id
    assert result.amount == Decimal("900.00")
    assert result.reimbursement_allocated == Decimal("900.00")
    assert result.reimbursement_remaining == Decimal("0.00")
    assert result.expense_allocated == Decimal("900.00")
    assert result.expense_remaining == Decimal("100.00")
    assert allocation_count(core_conn) == 1


def test_reimbursement_allocation_updates_and_deletes(core_conn, data_factory):
    """Verify allocation amounts can be adjusted and removed."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")
    created = create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("300.00"), conn=core_conn)

    updated = update_reimbursement_allocation_amount(created.id, Decimal("400.00"), conn=core_conn)
    deleted = delete_reimbursement_allocation(created.id, conn=core_conn)

    assert updated.amount == Decimal("400.00")
    assert updated.reimbursement_remaining == Decimal("500.00")
    assert updated.expense_remaining == Decimal("600.00")
    assert deleted is True
    assert allocation_count(core_conn) == 0


def test_reimbursable_expense_completion_completes_and_resumes_pending_balance(core_conn, data_factory):
    """Verify policy-limited reimbursement expenses can be completed and resumed."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")
    create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("900.00"), conn=core_conn)

    completion_id = complete_reimbursable_expense(expense_id, conn=core_conn)
    context = presenter.build_reimbursements_view_model(
        queries.fetch_reimbursement_transactions(core_conn),
        queries.fetch_reimbursable_expense_transactions(core_conn),
        queries.fetch_reimbursement_allocations(core_conn),
    )
    resumed = resume_reimbursable_expense(expense_id, conn=core_conn)

    assert completion_id > 0
    assert context["summary"]["pending_reimbursable_expenses"] == Decimal("0")
    assert context["expense_options"] == []
    assert context["reimbursable_expenses"][0]["status_label"] == "Complete"
    assert resumed is True


def test_action_needed_expenses_only_include_tagged_active_pending_rows(core_conn, data_factory):
    """Verify active expenses exclude untagged matches and completed tagged rows."""
    tagged_expense_id = expense_transaction(data_factory, amount="1000.00")
    untagged_expense_id = expense_transaction(data_factory, amount="1000.00", tags=("Conference",))
    completed_expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-100.00")
    create_reimbursement_allocation(reimbursement_id, untagged_expense_id, Decimal("100.00"), conn=core_conn)
    complete_reimbursable_expense(completed_expense_id, conn=core_conn)

    context = presenter.build_reimbursements_view_model(
        queries.fetch_reimbursement_transactions(core_conn),
        queries.fetch_reimbursable_expense_transactions(core_conn),
        queries.fetch_reimbursement_allocations(core_conn),
    )
    active_expense_ids = {row["id"] for row in context["action_needed"]["expenses"]}
    all_expense_ids = {row["id"] for row in context["reimbursable_expenses"]}

    assert active_expense_ids == {tagged_expense_id}
    assert {tagged_expense_id, untagged_expense_id, completed_expense_id}.issubset(all_expense_ids)


def test_ignored_reimbursement_allocation_no_longer_offsets_spending(core_conn, data_factory):
    """Verify ignored reimbursement credits stop reducing expense spending."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-400.00")
    create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("400.00"), conn=core_conn)

    assert spending_impact_for(core_conn, expense_id) == Decimal("600.00")

    set_ignored(core_conn, reimbursement_id)

    assert spending_impact_for(core_conn, expense_id) == Decimal("1000.00")
    assert queries.fetch_reimbursement_allocations(core_conn) == []


def test_reclassified_reimbursement_allocation_no_longer_offsets_spending(core_conn, data_factory):
    """Verify credits moved out of Reimbursement stop reducing expense spending."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-400.00")
    create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("400.00"), conn=core_conn)

    set_category(core_conn, reimbursement_id, "Income")

    assert spending_impact_for(core_conn, expense_id) == Decimal("1000.00")
    assert queries.fetch_reimbursement_allocations(core_conn) == []


def test_reimbursable_expenses_ignore_inactive_allocations(core_conn, data_factory):
    """Verify stale allocation rows do not keep untagged expenses in tracking."""
    untagged_expense_id = expense_transaction(data_factory, amount="1000.00", tags=("Conference",))
    reimbursement_id = reimbursement_transaction(data_factory, amount="-400.00")
    create_reimbursement_allocation(reimbursement_id, untagged_expense_id, Decimal("400.00"), conn=core_conn)

    assert untagged_expense_id in {row["id"] for row in queries.fetch_reimbursable_expense_transactions(core_conn)}

    set_ignored(core_conn, reimbursement_id)

    assert untagged_expense_id not in {row["id"] for row in queries.fetch_reimbursable_expense_transactions(core_conn)}


def test_reimbursement_allocation_rejects_duplicate_transaction_pair(core_conn, data_factory):
    """Verify a reimbursement and expense can only be linked once."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")
    create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("300.00"), conn=core_conn)

    with pytest.raises(ReimbursementAllocationError, match="already matched"):
        create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("100.00"), conn=core_conn)


def test_reimbursement_allocation_rejects_over_allocated_reimbursement(core_conn, data_factory):
    """Verify allocation totals cannot exceed the reimbursement credit."""
    first_expense_id = expense_transaction(data_factory, amount="800.00")
    second_expense_id = expense_transaction(data_factory, amount="300.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")
    create_reimbursement_allocation(reimbursement_id, first_expense_id, Decimal("800.00"), conn=core_conn)

    with pytest.raises(ReimbursementAllocationError, match="still unmatched"):
        create_reimbursement_allocation(reimbursement_id, second_expense_id, Decimal("150.00"), conn=core_conn)


def test_reimbursement_allocation_rejects_over_allocated_expense(core_conn, data_factory):
    """Verify allocation totals cannot exceed the covered expense amount."""
    expense_id = expense_transaction(data_factory, amount="500.00")
    first_reimbursement_id = reimbursement_transaction(data_factory, amount="-300.00")
    second_reimbursement_id = reimbursement_transaction(data_factory, amount="-300.00")
    create_reimbursement_allocation(first_reimbursement_id, expense_id, Decimal("300.00"), conn=core_conn)

    with pytest.raises(ReimbursementAllocationError, match="still to reimburse"):
        create_reimbursement_allocation(second_reimbursement_id, expense_id, Decimal("250.00"), conn=core_conn)


def test_reimbursement_allocation_requires_reimbursement_category(core_conn, data_factory):
    """Verify credits must use the dedicated Reimbursement category."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    income_id = reimbursement_transaction(data_factory, amount="-900.00", category="Income")

    with pytest.raises(ReimbursementAllocationError, match="Reimbursement category"):
        create_reimbursement_allocation(income_id, expense_id, Decimal("100.00"), conn=core_conn)


def test_reimbursement_allocation_rejects_ignored_transactions(core_conn, data_factory):
    """Verify direct submissions cannot match ignored reimbursement subjects."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    ignored_reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")
    active_reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")
    ignored_expense_id = expense_transaction(data_factory, amount="1000.00")
    set_ignored(core_conn, ignored_reimbursement_id)
    set_ignored(core_conn, ignored_expense_id)

    with pytest.raises(ReimbursementAllocationError, match="Reimbursement transaction must not be ignored"):
        create_reimbursement_allocation(ignored_reimbursement_id, expense_id, Decimal("100.00"), conn=core_conn)

    with pytest.raises(ReimbursementAllocationError, match="Expense transaction must not be ignored"):
        create_reimbursement_allocation(active_reimbursement_id, ignored_expense_id, Decimal("100.00"), conn=core_conn)

    assert allocation_count(core_conn) == 0


def test_reimbursement_allocation_requires_expense_role(core_conn, data_factory):
    """Verify the covered transaction must remain an expense row."""
    income_like_expense_id = expense_transaction(
        data_factory,
        amount="1000.00",
        category="Travel",
        transaction_kind=TRANSACTION_KIND_INCOME,
    )
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")

    with pytest.raises(ReimbursementAllocationError, match="expense cash-flow role"):
        create_reimbursement_allocation(reimbursement_id, income_like_expense_id, Decimal("100.00"), conn=core_conn)
