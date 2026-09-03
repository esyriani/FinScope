"""Read-side SQLAlchemy Core queries for reimbursement monitoring."""

from typing import Any

from sqlalchemy import func, or_, select

from finance_app.core.builtin_taxonomy import BUILTIN_TAG_REIMBURSABLE
from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.constants import TRANSACTION_KIND_EXPENSE
from finance_app.core.reimbursement_sql import (
    active_reimbursement_allocation_rows,
    active_reimbursement_allocation_totals_by_expense,
    active_reimbursement_allocation_totals_by_reimbursement,
    reimbursement_category_clause,
)
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import reimbursement_expense_completions as expense_completions_table
from finance_app.database.tables import tags as tags_table
from finance_app.database.tables import transaction_tags as transaction_tags_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.reimbursements.constants import REIMBURSABLE_TAG


def reimbursement_allocation_totals() -> Any:
    """Return active allocation totals grouped by reimbursement transaction."""
    return active_reimbursement_allocation_totals_by_reimbursement()


def expense_allocation_totals() -> Any:
    """Return active allocation totals grouped by expense transaction."""
    return active_reimbursement_allocation_totals_by_expense()


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
                transaction_category_label_expression(None).label("category"),
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
            or_(
                tags_table.c.builtin_key == BUILTIN_TAG_REIMBURSABLE,
                tags_table.c.name == REIMBURSABLE_TAG,
            ),
        )
        .correlate(transactions_table)
        .exists()
    )
    active_allocations = active_reimbursement_allocation_rows()
    has_allocation = (
        select(1)
        .select_from(active_allocations)
        .where(active_allocations.c.expense_transaction_id == transactions_table.c.id)
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
                transaction_category_label_expression(None).label("category"),
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
    active_allocations = active_reimbursement_allocation_rows()
    reimbursement_tx = transactions_table.alias("reimbursement_tx")
    expense_tx = transactions_table.alias("expense_tx")
    rows = (
        conn.execute(
            select(
                active_allocations.c.id,
                active_allocations.c.amount,
                active_allocations.c.reimbursement_transaction_id,
                active_allocations.c.expense_transaction_id,
                active_allocations.c.created_at,
                reimbursement_tx.c.tx_date.label("reimbursement_date"),
                reimbursement_tx.c.description.label("reimbursement_description"),
                reimbursement_tx.c.amount.label("reimbursement_amount"),
                expense_tx.c.tx_date.label("expense_date"),
                expense_tx.c.description.label("expense_description"),
                expense_tx.c.amount.label("expense_amount"),
                transaction_category_label_expression(None, transaction_table=expense_tx).label("expense_category"),
            )
            .select_from(
                active_allocations.join(
                    reimbursement_tx,
                    reimbursement_tx.c.id == active_allocations.c.reimbursement_transaction_id,
                ).join(
                    expense_tx,
                    expense_tx.c.id == active_allocations.c.expense_transaction_id,
                )
            )
            .order_by(
                reimbursement_tx.c.tx_date.desc(),
                expense_tx.c.tx_date.desc(),
                active_allocations.c.id.desc(),
            )
        )
        .mappings()
        .fetchall()
    )
    return [dict(row) for row in rows]
