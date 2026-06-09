"""Shared reporting predicates for analytics queries.

Provides SQLAlchemy Core expressions that define which transaction kinds are
visible to financial reports. Callers decide whether a filtered tag view should
include transfer credits as reimbursement offsets.
"""

from typing import Any

from sqlalchemy import and_, or_

from finance_app.core.constants import (
    NON_REPORTABLE_TRANSACTION_KINDS,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
)
from finance_app.database.tables import transactions as transactions_table


def reportable_transaction_clause() -> Any:
    """Return the standard reporting scope that excludes payments and transfers."""
    return transactions_table.c.transaction_kind.not_in(NON_REPORTABLE_TRANSACTION_KINDS)


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
        ),
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
    income_clause = transactions_table.c.transaction_kind == TRANSACTION_KIND_INCOME
    if not include_transfer_credits:
        return income_clause

    return or_(
        income_clause,
        tagged_transfer_credit_clause(),
    )
