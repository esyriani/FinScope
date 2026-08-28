"""Shared reporting predicates for analytics queries.

Provides SQLAlchemy Core expressions that define which transaction kinds are
visible to financial reports. Callers decide whether a filtered tag view should
include transfer credits as reimbursement offsets.
"""

from typing import Any

from sqlalchemy import and_, case, func, or_, select

from finance_app.core.builtin_taxonomy import BUILTIN_CATEGORY_REIMBURSEMENT
from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.constants import (
    NON_REPORTABLE_TRANSACTION_KINDS,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
)
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import reimbursement_allocations as reimbursement_allocations_table
from finance_app.database.tables import transactions as transactions_table


def reportable_transaction_clause() -> Any:
    """Return the standard reporting scope that excludes payments and transfers."""
    return and_(
        transactions_table.c.transaction_kind.not_in(NON_REPORTABLE_TRANSACTION_KINDS),
        ~reimbursement_credit_clause(),
    )


def reimbursement_credit_clause() -> Any:
    """Return reimbursement credits that should offset expenses through allocations."""
    return and_(
        transaction_has_builtin_category_clause(BUILTIN_CATEGORY_REIMBURSEMENT),
        transactions_table.c.amount < 0,
    )


def transaction_has_builtin_category_clause(builtin_key: str) -> Any:
    """Return a SQL predicate for transactions assigned a built-in category."""
    category_ids = select(categories_table.c.id).where(categories_table.c.builtin_key == builtin_key)
    category_names = select(categories_table.c.name).where(categories_table.c.builtin_key == builtin_key)
    return or_(
        and_(
            transactions_table.c.category_id.is_not(None),
            transactions_table.c.category_id.in_(category_ids),
        ),
        and_(
            transactions_table.c.category_id.is_(None),
            transaction_category_label_expression("").in_(category_names),
        ),
    )


def spending_impact_clause() -> Any:
    """Return rows that contribute to net spending totals.

    Refunds are negative reportable rows, so they reduce spending instead of
    being counted as income.
    """
    return or_(
        and_(
            transactions_table.c.transaction_kind == TRANSACTION_KIND_EXPENSE,
            transactions_table.c.amount > 0,
        ),
        and_(
            transactions_table.c.transaction_kind == TRANSACTION_KIND_REFUND,
            transactions_table.c.amount < 0,
            ~transaction_has_builtin_category_clause(BUILTIN_CATEGORY_REIMBURSEMENT),
        ),
    )


def reimbursed_expense_allocation_amount() -> Any:
    """Return a correlated allocation total for the current expense row."""
    return (
        select(func.coalesce(func.sum(reimbursement_allocations_table.c.amount), 0))
        .where(reimbursement_allocations_table.c.expense_transaction_id == transactions_table.c.id)
        .scalar_subquery()
    )


def spending_impact_amount_expression() -> Any:
    """Return the signed spending amount after reimbursement allocations."""
    return case(
        (
            and_(
                transactions_table.c.transaction_kind == TRANSACTION_KIND_EXPENSE,
                transactions_table.c.amount > 0,
            ),
            transactions_table.c.amount - reimbursed_expense_allocation_amount(),
        ),
        (
            and_(
                transactions_table.c.transaction_kind == TRANSACTION_KIND_REFUND,
                transactions_table.c.amount < 0,
                ~transaction_has_builtin_category_clause(BUILTIN_CATEGORY_REIMBURSEMENT),
            ),
            transactions_table.c.amount,
        ),
        else_=0,
    )


def income_amount_expression() -> Any:
    """Return the positive income amount for ordinary income rows."""
    return -transactions_table.c.amount


def cashflow_amount_expression() -> Any:
    """Return signed net cash-flow impact after reimbursement allocations."""
    return case(
        (
            spending_impact_clause(),
            -spending_impact_amount_expression(),
        ),
        else_=income_amount_expression(),
    )


def tagged_transfer_credit_clause() -> Any:
    """Return the transfer-credit scope used after callers apply tag filters."""
    return and_(
        transactions_table.c.transaction_kind == TRANSACTION_KIND_TRANSFER,
        transactions_table.c.amount < 0,
    )


def reportable_or_tagged_transfer_credit_clause(include_transfer_credits: bool = False) -> Any:
    """Return reportable rows, optionally including tagged reimbursement credits."""
    if not include_transfer_credits:
        return reportable_transaction_clause()

    return or_(
        reportable_transaction_clause(),
        tagged_transfer_credit_clause(),
    )


def income_or_tagged_transfer_credit_clause(include_transfer_credits: bool = False) -> Any:
    """Return income rows, optionally including tagged reimbursement credits."""
    income_clause = and_(
        transactions_table.c.transaction_kind == TRANSACTION_KIND_INCOME,
        ~reimbursement_credit_clause(),
    )
    if not include_transfer_credits:
        return income_clause

    return or_(
        income_clause,
        tagged_transfer_credit_clause(),
    )
