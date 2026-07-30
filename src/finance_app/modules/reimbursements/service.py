"""Application service for reimbursement allocation workflows.

The service validates transaction roles and allocation limits before delegating
Core persistence to the reimbursement repository. Callers may pass an active
connection so UI routes and background workflows can share their transaction.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from finance_app.core.config import settings
from finance_app.core.constants import TRANSACTION_KIND_EXPENSE
from finance_app.core.money import MoneyValue, money_to_decimal, quantize_money
from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories.builtins import (
    BUILTIN_CATEGORY_REIMBURSEMENT,
    BUILTIN_TAG_REIMBURSABLE,
    builtin_tag_by_key,
    is_category_name_for_builtin_key,
)
from finance_app.modules.categories.taxonomy import get_tag_color_map, get_transaction_tags_by_id, upsert_tag_metadata
from finance_app.modules.reimbursements import presenter, queries, repository
from finance_app.modules.reimbursements.constants import REIMBURSABLE_TAG
from finance_app.modules.settings.runtime import get_int_setting


class ReimbursementAllocationError(ValueError):
    """Raised when a reimbursement allocation violates domain rules."""


@dataclass(frozen=True)
class ReimbursementAllocationResult:
    """Represent the allocation state after a reimbursement write."""

    id: int
    reimbursement_transaction_id: int
    expense_transaction_id: int
    amount: Decimal
    reimbursement_allocated: Decimal
    reimbursement_remaining: Decimal
    expense_allocated: Decimal
    expense_remaining: Decimal


def build_reimbursements_context() -> dict[str, Any]:
    """Build the reimbursement monitoring page context."""
    return build_reimbursements_context_from_args({})


def build_reimbursements_context_from_args(args: Any) -> dict[str, Any]:
    """Build the reimbursement monitoring page context for request filters."""
    with db_core_transaction() as conn:
        reimbursement_rows = queries.fetch_reimbursement_transactions(conn)
        expense_rows = queries.fetch_reimbursable_expense_transactions(conn)
        allocation_rows = queries.fetch_reimbursement_allocations(conn)
        expense_tag_map = get_transaction_tags_by_id(conn, [row["id"] for row in expense_rows])
        tag_colors = get_tag_color_map(conn)
        table_page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)

    context = presenter.build_reimbursements_view_model(
        reimbursement_rows,
        expense_rows,
        allocation_rows,
        expense_tag_map,
        tag_colors,
        args,
    )
    context["table_page_size"] = table_page_size
    return context


def create_reimbursement_allocation(
    reimbursement_transaction_id: object,
    expense_transaction_id: object,
    amount: MoneyValue,
    conn: Any | None = None,
) -> ReimbursementAllocationResult:
    """Create a reimbursement allocation after validating both transactions.

    Args:
        reimbursement_transaction_id: Transaction id for the incoming credit.
        expense_transaction_id: Transaction id for the covered expense.
        amount: Positive amount to allocate from the reimbursement to the expense.
        conn: Optional active Core connection.

    Returns:
        Allocation state after the insert.

    Raises:
        ReimbursementAllocationError: If ids, transaction roles, or allocation
            limits are invalid.
    """
    with db_core_transaction(conn) as active_conn:
        return _save_reimbursement_allocation(
            active_conn,
            allocation_id=None,
            reimbursement_transaction_id=reimbursement_transaction_id,
            expense_transaction_id=expense_transaction_id,
            amount=amount,
        )


def create_reimbursement_matches(
    reimbursement_transaction_id: object,
    expense_matches: Sequence[tuple[object, MoneyValue]],
    conn: Any | None = None,
) -> list[ReimbursementAllocationResult]:
    """Create one or more reimbursement matches for the same incoming credit.

    Args:
        reimbursement_transaction_id: Transaction id for the incoming credit.
        expense_matches: Expense transaction ids with the amount matched to each.
        conn: Optional active Core connection.

    Returns:
        Allocation states after all inserts.

    Raises:
        ReimbursementAllocationError: If no expenses were selected or any match
            violates transaction-role or remaining-balance limits.
    """
    if not expense_matches:
        raise ReimbursementAllocationError("Select at least one expense to match.")

    with db_core_transaction(conn) as active_conn:
        return [
            _save_reimbursement_allocation(
                active_conn,
                allocation_id=None,
                reimbursement_transaction_id=reimbursement_transaction_id,
                expense_transaction_id=expense_transaction_id,
                amount=amount,
            )
            for expense_transaction_id, amount in expense_matches
        ]


def create_expense_reimbursement_matches(
    expense_transaction_id: object,
    reimbursement_matches: Sequence[tuple[object, MoneyValue]],
    conn: Any | None = None,
) -> list[ReimbursementAllocationResult]:
    """Create one or more reimbursement matches for the same expense.

    Args:
        expense_transaction_id: Expense transaction id receiving reimbursement.
        reimbursement_matches: Reimbursement transaction ids with the amount
            matched from each credit.
        conn: Optional active Core connection.

    Returns:
        Allocation states after all inserts.

    Raises:
        ReimbursementAllocationError: If no reimbursements were selected or any
            match violates transaction-role or remaining-balance limits.
    """
    if not reimbursement_matches:
        raise ReimbursementAllocationError("Select at least one reimbursement to match.")

    with db_core_transaction(conn) as active_conn:
        return [
            _save_reimbursement_allocation(
                active_conn,
                allocation_id=None,
                reimbursement_transaction_id=reimbursement_transaction_id,
                expense_transaction_id=expense_transaction_id,
                amount=amount,
            )
            for reimbursement_transaction_id, amount in reimbursement_matches
        ]


def update_reimbursement_allocation_amount(
    allocation_id: object,
    amount: MoneyValue,
    conn: Any | None = None,
) -> ReimbursementAllocationResult:
    """Update an existing reimbursement allocation amount.

    Args:
        allocation_id: Existing reimbursement allocation id.
        amount: Positive replacement amount.
        conn: Optional active Core connection.

    Returns:
        Allocation state after the update.

    Raises:
        ReimbursementAllocationError: If the allocation does not exist or the
            replacement amount violates transaction limits.
    """
    normalized_allocation_id = positive_int(allocation_id, "match id")
    with db_core_transaction(conn) as active_conn:
        allocation = repository.get_allocation(active_conn, normalized_allocation_id)
        if allocation is None:
            raise ReimbursementAllocationError("Reimbursement match was not found.")

        return _save_reimbursement_allocation(
            active_conn,
            allocation_id=normalized_allocation_id,
            reimbursement_transaction_id=allocation["reimbursement_transaction_id"],
            expense_transaction_id=allocation["expense_transaction_id"],
            amount=amount,
        )


def delete_reimbursement_allocation(allocation_id: object, conn: Any | None = None) -> bool:
    """Delete a reimbursement allocation by id.

    Args:
        allocation_id: Existing reimbursement allocation id.
        conn: Optional active Core connection.

    Returns:
        ``True`` when a row was deleted, otherwise ``False``.
    """
    normalized_allocation_id = positive_int(allocation_id, "match id")
    with db_core_transaction(conn) as active_conn:
        return repository.delete_allocation(active_conn, normalized_allocation_id)


def complete_reimbursable_expense(expense_transaction_id: object, conn: Any | None = None) -> int:
    """Mark a reimbursable expense complete without adding fake reimbursement money.

    Args:
        expense_transaction_id: Transaction id for the expense to complete.
        conn: Optional active Core connection.

    Returns:
        The completion marker id.

    Raises:
        ReimbursementAllocationError: If the transaction cannot be reconciled as
            a reimbursable expense.
    """
    normalized_expense_id = positive_int(expense_transaction_id, "expense transaction id")
    with db_core_transaction(conn) as active_conn:
        expense = require_transaction(active_conn, normalized_expense_id, "Expense transaction")
        validate_expense_transaction(expense)
        return repository.insert_expense_completion(active_conn, normalized_expense_id)


def resume_reimbursable_expense(expense_transaction_id: object, conn: Any | None = None) -> bool:
    """Return a completed reimbursable expense to reimbursement tracking."""
    normalized_expense_id = positive_int(expense_transaction_id, "expense transaction id")
    with db_core_transaction(conn) as active_conn:
        return repository.delete_expense_completion(active_conn, normalized_expense_id)


def set_expense_reimbursable_tag(
    expense_transaction_id: object,
    enabled: bool,
    conn: Any | None = None,
) -> bool:
    """Add or remove the Reimbursable tag for one expense transaction."""
    normalized_expense_id = positive_int(expense_transaction_id, "expense transaction id")
    with db_core_transaction(conn) as active_conn:
        expense = require_transaction(active_conn, normalized_expense_id, "Expense transaction")
        validate_expense_transaction(expense)
        ensure_reimbursable_tag(active_conn)
        return repository.set_transaction_tag_state(
            active_conn,
            normalized_expense_id,
            REIMBURSABLE_TAG,
            enabled,
            builtin_key=BUILTIN_TAG_REIMBURSABLE,
        )


def _save_reimbursement_allocation(
    conn: Any,
    *,
    allocation_id: int | None,
    reimbursement_transaction_id: object,
    expense_transaction_id: object,
    amount: MoneyValue,
) -> ReimbursementAllocationResult:
    """Validate and persist an insert or update allocation operation."""
    normalized_reimbursement_id = positive_int(reimbursement_transaction_id, "reimbursement transaction id")
    normalized_expense_id = positive_int(expense_transaction_id, "expense transaction id")
    if normalized_reimbursement_id == normalized_expense_id:
        raise ReimbursementAllocationError("A reimbursement cannot be matched to itself.")

    normalized_amount = positive_money(amount)
    locked_transactions = repository.lock_transaction_allocation_subjects(
        conn,
        (normalized_reimbursement_id, normalized_expense_id),
    )
    reimbursement = require_transaction_subject(
        locked_transactions,
        normalized_reimbursement_id,
        "Reimbursement transaction",
    )
    expense = require_transaction_subject(locked_transactions, normalized_expense_id, "Expense transaction")
    validate_reimbursement_transaction(reimbursement)
    validate_expense_transaction(expense)

    if allocation_id is None and repository.get_allocation_pair(
        conn,
        normalized_reimbursement_id,
        normalized_expense_id,
    ):
        raise ReimbursementAllocationError("Those transactions are already matched.")

    reimbursement_allocated_before = money_to_decimal(
        repository.sum_allocated_to_reimbursement(
            conn,
            normalized_reimbursement_id,
            exclude_allocation_id=allocation_id,
        )
    )
    expense_allocated_before = money_to_decimal(
        repository.sum_allocated_to_expense(
            conn,
            normalized_expense_id,
            exclude_allocation_id=allocation_id,
        )
    )
    reimbursement_limit = abs(money_to_decimal(reimbursement["amount"]))
    expense_limit = money_to_decimal(expense["amount"])

    if reimbursement_allocated_before + normalized_amount > reimbursement_limit:
        raise ReimbursementAllocationError("The match amount exceeds the reimbursement amount still unmatched.")
    if expense_allocated_before + normalized_amount > expense_limit:
        raise ReimbursementAllocationError("The match amount exceeds the expense amount still to reimburse.")

    if allocation_id is None:
        allocation_id = repository.insert_allocation(
            conn,
            normalized_reimbursement_id,
            normalized_expense_id,
            normalized_amount,
        )
    else:
        repository.update_allocation_amount(conn, allocation_id, normalized_amount)

    reimbursement_allocated = money_to_decimal(
        repository.sum_allocated_to_reimbursement(conn, normalized_reimbursement_id)
    )
    expense_allocated = money_to_decimal(repository.sum_allocated_to_expense(conn, normalized_expense_id))

    return ReimbursementAllocationResult(
        id=allocation_id,
        reimbursement_transaction_id=normalized_reimbursement_id,
        expense_transaction_id=normalized_expense_id,
        amount=normalized_amount,
        reimbursement_allocated=reimbursement_allocated,
        reimbursement_remaining=reimbursement_limit - reimbursement_allocated,
        expense_allocated=expense_allocated,
        expense_remaining=expense_limit - expense_allocated,
    )


def require_transaction(conn: Any, transaction_id: int, label: str) -> dict[str, Any]:
    """Return a transaction allocation subject or raise a domain error."""
    transaction = repository.get_transaction_allocation_subject(conn, transaction_id)
    if transaction is None:
        raise ReimbursementAllocationError(f"{label} was not found.")
    return transaction


def require_transaction_subject(
    transactions_by_id: dict[int, dict[str, Any]],
    transaction_id: int,
    label: str,
) -> dict[str, Any]:
    """Return a locked transaction allocation subject or raise a domain error."""
    transaction = transactions_by_id.get(transaction_id)
    if transaction is None:
        raise ReimbursementAllocationError(f"{label} was not found.")
    return transaction


def validate_reimbursement_transaction(transaction: dict[str, Any]) -> None:
    """Validate that a transaction can provide reimbursement funds."""
    if not is_reimbursement_category(transaction):
        raise ReimbursementAllocationError("Reimbursement transaction must use the Reimbursement category.")
    if money_to_decimal(transaction["amount"]) >= 0:
        raise ReimbursementAllocationError("Reimbursement transaction must be an incoming credit.")


def validate_expense_transaction(transaction: dict[str, Any]) -> None:
    """Validate that a transaction can receive reimbursement funds."""
    if money_to_decimal(transaction["amount"]) <= 0:
        raise ReimbursementAllocationError("Expense transaction must be a positive spending row.")
    if transaction["transaction_kind"] != TRANSACTION_KIND_EXPENSE:
        raise ReimbursementAllocationError("Expense transaction must have the expense cash-flow role.")
    if is_reimbursement_category(transaction):
        raise ReimbursementAllocationError("Expense transaction cannot use the Reimbursement category.")


def is_reimbursement_category(transaction: dict[str, Any]) -> bool:
    """Return whether a transaction is categorized as a reimbursement credit."""
    if transaction.get("category_builtin_key") == BUILTIN_CATEGORY_REIMBURSEMENT:
        return True
    return transaction.get("category_id") is None and is_category_name_for_builtin_key(
        transaction.get("category"),
        BUILTIN_CATEGORY_REIMBURSEMENT,
    )


def ensure_reimbursable_tag(conn: Any) -> None:
    """Ensure the built-in reimbursable tag row exists before assignment."""
    tag = builtin_tag_by_key(BUILTIN_TAG_REIMBURSABLE)
    if tag is None:
        return
    upsert_tag_metadata(
        conn,
        tag.name,
        tag.description,
        tag.instruction,
        tag.color,
        builtin_key=tag.key,
    )


def positive_money(value: MoneyValue) -> Decimal:
    """Return a positive fixed-scale money amount or raise a domain error."""
    amount = quantize_money(value)
    if amount is None or amount <= 0:
        raise ReimbursementAllocationError("Match amount must be greater than zero.")
    return amount


def positive_int(value: object, label: str) -> int:
    """Return a positive integer id or raise a domain error."""
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ReimbursementAllocationError(f"Invalid {label}.") from exc
    if parsed <= 0:
        raise ReimbursementAllocationError(f"Invalid {label}.")
    return parsed
