"""Read-side SQLAlchemy Core queries for reimbursement monitoring."""

from typing import Any

from sqlalchemy import and_, func, or_, select

from finance_app.core.constants import REIMBURSEMENT_CATEGORY, TRANSACTION_KIND_EXPENSE
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import reimbursement_allocations as reimbursement_allocations_table
from finance_app.database.tables import reimbursement_expense_completions as expense_completions_table
from finance_app.database.tables import tags as tags_table
from finance_app.database.tables import transaction_tags as transaction_tags_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.builtins import BUILTIN_CATEGORY_REIMBURSEMENT
from finance_app.modules.reimbursements.constants import REIMBURSABLE_TAG


def reimbursement_category_clause(transaction_table: Any, category_table: Any) -> Any:
    """Return a predicate for rows categorized as reimbursement credits."""
    return or_(
        category_table.c.builtin_key == BUILTIN_CATEGORY_REIMBURSEMENT,
        transaction_table.c.category == REIMBURSEMENT_CATEGORY,
    )


def reimbursement_allocation_totals() -> Any:
    """Return allocation totals grouped by reimbursement transaction."""
    return (
        select(
            reimbursement_allocations_table.c.reimbursement_transaction_id.label("transaction_id"),
            func.coalesce(func.sum(reimbursement_allocations_table.c.amount), 0).label("allocated"),
        )
        .group_by(reimbursement_allocations_table.c.reimbursement_transaction_id)
        .subquery()
    )


def expense_allocation_totals() -> Any:
    """Return allocation totals grouped by expense transaction."""
    return (
        select(
            reimbursement_allocations_table.c.expense_transaction_id.label("transaction_id"),
            func.coalesce(func.sum(reimbursement_allocations_table.c.amount), 0).label("allocated"),
        )
        .group_by(reimbursement_allocations_table.c.expense_transaction_id)
        .subquery()
    )


def fetch_reimbursement_transactions(conn: Any) -> list[dict[str, Any]]:
    """Fetch reimbursement credit transactions with allocation totals."""
    allocated = reimbursement_allocation_totals()
    rows = (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transactions_table.c.category,
                transactions_table.c.transaction_kind,
                func.coalesce(allocated.c.allocated, 0).label("allocated"),
            )
            .select_from(
                transactions_table.outerjoin(
                    categories_table,
                    categories_table.c.id == transactions_table.c.category_id,
                ).outerjoin(
                    allocated,
                    allocated.c.transaction_id == transactions_table.c.id,
                )
            )
            .where(
                transactions_table.c.ignored == 0,
                transactions_table.c.amount < 0,
                reimbursement_category_clause(transactions_table, categories_table),
            )
            .order_by(transactions_table.c.tx_date.desc(), transactions_table.c.id.desc())
        )
        .mappings()
        .fetchall()
    )
    return [dict(row) for row in rows]


def fetch_reimbursable_expense_transactions(conn: Any) -> list[dict[str, Any]]:
    """Fetch expense transactions that are reimbursable, matched, or completed."""
    allocated = expense_allocation_totals()
    has_reimbursable_tag = (
        select(1)
        .select_from(
            transaction_tags_table.join(
                tags_table,
                tags_table.c.id == transaction_tags_table.c.tag_id,
            )
        )
        .where(
            transaction_tags_table.c.transaction_id == transactions_table.c.id,
            tags_table.c.name == REIMBURSABLE_TAG,
        )
        .correlate(transactions_table)
        .exists()
    )
    has_allocation = (
        select(1)
        .select_from(reimbursement_allocations_table)
        .where(reimbursement_allocations_table.c.expense_transaction_id == transactions_table.c.id)
        .correlate(transactions_table)
        .exists()
    )
    has_completion = (
        select(1)
        .select_from(expense_completions_table)
        .where(expense_completions_table.c.expense_transaction_id == transactions_table.c.id)
        .correlate(transactions_table)
        .exists()
    )
    rows = (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transactions_table.c.category,
                transactions_table.c.transaction_kind,
                accounts_table.c.name.label("account_name"),
                func.coalesce(allocated.c.allocated, 0).label("allocated"),
                has_reimbursable_tag.label("has_reimbursable_tag"),
                expense_completions_table.c.id.label("completion_id"),
                expense_completions_table.c.created_at.label("completed_at"),
            )
            .select_from(
                transactions_table.outerjoin(
                    accounts_table,
                    accounts_table.c.id == transactions_table.c.account_id,
                )
                .outerjoin(
                    allocated,
                    allocated.c.transaction_id == transactions_table.c.id,
                )
                .outerjoin(
                    expense_completions_table,
                    expense_completions_table.c.expense_transaction_id == transactions_table.c.id,
                )
            )
            .where(
                transactions_table.c.ignored == 0,
                transactions_table.c.amount > 0,
                transactions_table.c.transaction_kind == TRANSACTION_KIND_EXPENSE,
                or_(has_reimbursable_tag, has_allocation, has_completion),
            )
            .order_by(transactions_table.c.tx_date.desc(), transactions_table.c.id.desc())
        )
        .mappings()
        .fetchall()
    )
    return [dict(row) for row in rows]


def fetch_reimbursement_allocations(conn: Any) -> list[dict[str, Any]]:
    """Fetch allocation rows with both linked transaction labels."""
    reimbursement_tx = transactions_table.alias("reimbursement_tx")
    expense_tx = transactions_table.alias("expense_tx")
    rows = (
        conn.execute(
            select(
                reimbursement_allocations_table.c.id,
                reimbursement_allocations_table.c.amount,
                reimbursement_allocations_table.c.reimbursement_transaction_id,
                reimbursement_allocations_table.c.expense_transaction_id,
                reimbursement_allocations_table.c.created_at,
                reimbursement_tx.c.tx_date.label("reimbursement_date"),
                reimbursement_tx.c.description.label("reimbursement_description"),
                reimbursement_tx.c.amount.label("reimbursement_amount"),
                expense_tx.c.tx_date.label("expense_date"),
                expense_tx.c.description.label("expense_description"),
                expense_tx.c.amount.label("expense_amount"),
                expense_tx.c.category.label("expense_category"),
            )
            .select_from(
                reimbursement_allocations_table.join(
                    reimbursement_tx,
                    reimbursement_tx.c.id == reimbursement_allocations_table.c.reimbursement_transaction_id,
                ).join(
                    expense_tx,
                    expense_tx.c.id == reimbursement_allocations_table.c.expense_transaction_id,
                )
            )
            .where(
                and_(
                    reimbursement_tx.c.ignored == 0,
                    expense_tx.c.ignored == 0,
                )
            )
            .order_by(
                reimbursement_tx.c.tx_date.desc(),
                expense_tx.c.tx_date.desc(),
                reimbursement_allocations_table.c.id.desc(),
            )
        )
        .mappings()
        .fetchall()
    )
    return [dict(row) for row in rows]
