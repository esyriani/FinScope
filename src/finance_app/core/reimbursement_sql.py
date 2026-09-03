"""Shared SQL helpers for active reimbursement allocation rows.

These helpers centralize the reimbursement allocation lifecycle predicates used
by reporting and reimbursement workflows. An allocation is active only while
both linked transactions remain unignored and still satisfy their respective
reimbursement-credit and expense roles.
"""

from typing import Any

from sqlalchemy import and_, func, or_, select

from finance_app.core.builtin_taxonomy import (
    BUILTIN_CATEGORY_REIMBURSEMENT,
    builtin_category_name_for_key,
)
from finance_app.core.constants import TRANSACTION_KIND_EXPENSE
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import reimbursement_allocations as reimbursement_allocations_table
from finance_app.database.tables import transactions as transactions_table

REIMBURSEMENT_CATEGORY_NAME = builtin_category_name_for_key(BUILTIN_CATEGORY_REIMBURSEMENT)


def reimbursement_category_clause(transaction_table: Any, category_table: Any) -> Any:
    """Return a predicate for rows categorized as reimbursement credits."""
    return or_(
        category_table.c.builtin_key == BUILTIN_CATEGORY_REIMBURSEMENT,
        and_(
            transaction_table.c.category_id.is_(None),
            transaction_table.c.category == REIMBURSEMENT_CATEGORY_NAME,
        ),
    )


def non_reimbursement_category_clause(transaction_table: Any, category_table: Any) -> Any:
    """Return a null-safe predicate for rows outside the Reimbursement category."""
    category_is_not_reimbursement = or_(
        category_table.c.builtin_key.is_(None),
        category_table.c.builtin_key != BUILTIN_CATEGORY_REIMBURSEMENT,
    )
    legacy_category_is_not_reimbursement = or_(
        transaction_table.c.category_id.is_not(None),
        transaction_table.c.category.is_(None),
        transaction_table.c.category != REIMBURSEMENT_CATEGORY_NAME,
    )
    return and_(category_is_not_reimbursement, legacy_category_is_not_reimbursement)


def active_reimbursement_allocation_rows() -> Any:
    """Return active allocation rows with current linked transaction validation."""
    reimbursement_tx = transactions_table.alias()
    expense_tx = transactions_table.alias()
    reimbursement_category = categories_table.alias()
    expense_category = categories_table.alias()
    return (
        select(
            reimbursement_allocations_table.c.id,
            reimbursement_allocations_table.c.reimbursement_transaction_id,
            reimbursement_allocations_table.c.expense_transaction_id,
            reimbursement_allocations_table.c.amount,
            reimbursement_allocations_table.c.created_at,
        )
        .select_from(
            reimbursement_allocations_table.join(
                reimbursement_tx,
                reimbursement_tx.c.id == reimbursement_allocations_table.c.reimbursement_transaction_id,
            )
            .join(
                expense_tx,
                expense_tx.c.id == reimbursement_allocations_table.c.expense_transaction_id,
            )
            .outerjoin(
                reimbursement_category,
                reimbursement_category.c.id == reimbursement_tx.c.category_id,
            )
            .outerjoin(
                expense_category,
                expense_category.c.id == expense_tx.c.category_id,
            )
        )
        .where(
            reimbursement_tx.c.ignored == 0,
            reimbursement_tx.c.amount < 0,
            reimbursement_category_clause(reimbursement_tx, reimbursement_category),
            expense_tx.c.ignored == 0,
            expense_tx.c.amount > 0,
            expense_tx.c.transaction_kind == TRANSACTION_KIND_EXPENSE,
            non_reimbursement_category_clause(expense_tx, expense_category),
        )
        .subquery()
    )


def active_reimbursement_allocation_totals_by_expense() -> Any:
    """Return active allocation totals grouped by expense transaction."""
    active_allocations = active_reimbursement_allocation_rows()
    return (
        select(
            active_allocations.c.expense_transaction_id.label("transaction_id"),
            func.coalesce(func.sum(active_allocations.c.amount), 0).label("allocated"),
        )
        .group_by(active_allocations.c.expense_transaction_id)
        .subquery()
    )


def active_reimbursement_allocation_totals_by_reimbursement() -> Any:
    """Return active allocation totals grouped by reimbursement transaction."""
    active_allocations = active_reimbursement_allocation_rows()
    return (
        select(
            active_allocations.c.reimbursement_transaction_id.label("transaction_id"),
            func.coalesce(func.sum(active_allocations.c.amount), 0).label("allocated"),
        )
        .group_by(active_allocations.c.reimbursement_transaction_id)
        .subquery()
    )


def active_reimbursed_expense_allocation_amount(expense_transaction_id: Any) -> Any:
    """Return active allocation total for one expense transaction id expression."""
    active_allocations = active_reimbursement_allocation_rows()
    return (
        select(func.coalesce(func.sum(active_allocations.c.amount), 0))
        .where(active_allocations.c.expense_transaction_id == expense_transaction_id)
        .scalar_subquery()
    )
