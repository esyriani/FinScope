"""Presentation shaping for reimbursement monitoring views."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from finance_app.core.money import money_to_decimal

MATCH_CANDIDATE_LIMIT = 5


def build_reimbursements_view_model(
    reimbursement_rows: Sequence[dict[str, Any]],
    expense_rows: Sequence[dict[str, Any]],
    allocation_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Build a template-friendly reimbursement monitoring model."""
    reimbursements = [build_reimbursement_row(row) for row in reimbursement_rows]
    expenses = [build_expense_row(row) for row in expense_rows]
    allocations = [build_allocation_row(row) for row in allocation_rows]
    allocations = with_allocation_update_limits(allocations)
    reimbursement_options = [row for row in reimbursements if row["remaining"] > 0]
    expense_options = [row for row in expenses if row["pending_remaining"] > 0]
    return {
        "summary": build_summary(reimbursements, expenses, allocations),
        "reimbursements": reimbursements,
        "reimbursable_expenses": expenses,
        "allocations": allocations,
        "reimbursement_options": reimbursement_options,
        "expense_options": expense_options,
        "action_needed": build_action_needed(reimbursement_options, expense_options, allocations),
    }


def build_reimbursement_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return one display row for an incoming reimbursement credit."""
    amount = abs(money_to_decimal(row["amount"]))
    allocated = money_to_decimal(row["allocated"])
    remaining = amount - allocated
    return {
        "id": row["id"],
        "date": row["tx_date"],
        "description": row["description"],
        "amount": amount,
        "allocated": allocated,
        "remaining": remaining,
        "matched_percent": percentage(allocated, amount),
        "status_label": reimbursement_status_label(allocated, remaining),
        "status_class": reimbursement_status_class(allocated, remaining),
    }


def build_expense_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return one display row for an expense that can be reimbursed."""
    amount = money_to_decimal(row["amount"])
    allocated = money_to_decimal(row["allocated"])
    remaining = amount - allocated
    completed = bool(row.get("completion_id"))
    pending_remaining = Decimal("0") if completed else remaining
    return {
        "id": row["id"],
        "date": row["tx_date"],
        "description": row["description"],
        "category": row["category"],
        "amount": amount,
        "allocated": allocated,
        "remaining": remaining,
        "pending_remaining": pending_remaining,
        "is_complete": completed,
        "completed_at": row.get("completed_at"),
        "reimbursed_percent": percentage(allocated, amount),
        "status_label": expense_status_label(allocated, remaining, completed),
        "status_class": expense_status_class(allocated, remaining, completed),
    }


def build_allocation_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return one display row for an allocation link."""
    return {
        "id": row["id"],
        "amount": money_to_decimal(row["amount"]),
        "created_at": row["created_at"],
        "reimbursement_transaction_id": row["reimbursement_transaction_id"],
        "expense_transaction_id": row["expense_transaction_id"],
        "reimbursement_date": row["reimbursement_date"],
        "reimbursement_description": row["reimbursement_description"],
        "reimbursement_amount": abs(money_to_decimal(row["reimbursement_amount"])),
        "expense_date": row["expense_date"],
        "expense_description": row["expense_description"],
        "expense_amount": money_to_decimal(row["expense_amount"]),
        "expense_category": row["expense_category"],
    }


def with_allocation_update_limits(allocations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add per-row edit maximums for existing reimbursement matches."""
    reimbursement_totals: dict[int, Decimal] = {}
    expense_totals: dict[int, Decimal] = {}
    for row in allocations:
        reimbursement_id = int(row["reimbursement_transaction_id"])
        expense_id = int(row["expense_transaction_id"])
        reimbursement_totals[reimbursement_id] = (
            reimbursement_totals.get(reimbursement_id, Decimal("0")) + row["amount"]
        )
        expense_totals[expense_id] = expense_totals.get(expense_id, Decimal("0")) + row["amount"]

    limited_rows = []
    for row in allocations:
        reimbursement_id = int(row["reimbursement_transaction_id"])
        expense_id = int(row["expense_transaction_id"])
        amount = row["amount"]
        reimbursement_limit = row["reimbursement_amount"]
        expense_limit = row["expense_amount"]
        reimbursement_other = reimbursement_totals[reimbursement_id] - amount
        expense_other = expense_totals[expense_id] - amount
        max_amount = min(reimbursement_limit - reimbursement_other, expense_limit - expense_other)
        limited_rows.append({**row, "max_amount": max(max_amount, Decimal("0"))})
    return limited_rows


def build_summary(
    reimbursements: Sequence[dict[str, Any]],
    expenses: Sequence[dict[str, Any]],
    allocations: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Return aggregate metrics for the reimbursement page."""
    total_reimbursed = sum((row["amount"] for row in reimbursements), Decimal("0"))
    total_allocated = sum((row["amount"] for row in allocations), Decimal("0"))
    pending_credits = sum((positive_remaining(row) for row in reimbursements), Decimal("0"))
    pending_expenses = sum((positive_pending_remaining(row) for row in expenses), Decimal("0"))
    pending_credit_count = sum(1 for row in reimbursements if positive_remaining(row) > 0)
    pending_expense_count = sum(1 for row in expenses if positive_pending_remaining(row) > 0)
    return {
        "reimbursement_count": len(reimbursements),
        "expense_count": len(expenses),
        "allocation_count": len(allocations),
        "total_reimbursed": total_reimbursed,
        "total_allocated": total_allocated,
        "pending_reimbursement_credits": pending_credits,
        "pending_reimbursement_count": pending_credit_count,
        "pending_reimbursable_expenses": pending_expenses,
        "pending_expense_count": pending_expense_count,
    }


def build_action_needed(
    reimbursements: Sequence[dict[str, Any]],
    expenses: Sequence[dict[str, Any]],
    allocations: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Return unresolved items and contextual match candidates."""
    matched_pairs = {(row["reimbursement_transaction_id"], row["expense_transaction_id"]) for row in allocations}
    reimbursement_items = [
        {
            **row,
            **build_match_candidate_summary(row, expenses, matched_pairs),
        }
        for row in reimbursements
    ]
    return {
        "reimbursements": reimbursement_items,
        "expenses": list(expenses),
        "first_reimbursement_id": reimbursement_items[0]["id"] if reimbursement_items else None,
        "has_items": bool(reimbursements or expenses),
        "total_reimbursement_count": len(reimbursements),
        "total_expense_count": len(expenses),
    }


def build_match_candidate_summary(
    reimbursement: dict[str, Any],
    expenses: Sequence[dict[str, Any]],
    matched_pairs: set[tuple[int, int]],
) -> dict[str, Any]:
    """Return a limited set of candidate expenses for one reimbursement."""
    reimbursement_remaining = positive_remaining(reimbursement)
    candidates: list[dict[str, Any]] = []
    candidate_count = 0
    for expense in expenses:
        expense_remaining = positive_pending_remaining(expense)
        if expense_remaining <= 0:
            continue
        if (reimbursement["id"], expense["id"]) in matched_pairs:
            continue

        candidate_count += 1
        if len(candidates) >= MATCH_CANDIDATE_LIMIT:
            continue

        max_amount = min(reimbursement_remaining, expense_remaining)
        candidates.append(
            {
                **expense,
                "default_amount": max_amount,
                "max_amount": max_amount,
            }
        )
    return {
        "match_candidates": candidates,
        "match_candidate_count": candidate_count,
        "hidden_match_candidate_count": max(0, candidate_count - len(candidates)),
    }


def positive_remaining(row: dict[str, Any]) -> Decimal:
    """Return a non-negative remaining amount for summary totals."""
    return max(row["remaining"], Decimal("0"))


def positive_pending_remaining(row: dict[str, Any]) -> Decimal:
    """Return non-negative remaining amount still needing reimbursement action."""
    return max(row["pending_remaining"], Decimal("0"))


def percentage(part: Decimal, total: Decimal) -> int:
    """Return a bounded whole-number percentage for progress indicators."""
    if total <= 0:
        return 0
    return min(100, max(0, int((part / total) * 100)))


def reimbursement_status_label(allocated: Decimal, remaining: Decimal) -> str:
    """Return the status label for a reimbursement credit."""
    if remaining <= 0:
        return "Fully matched"
    if allocated > 0:
        return "Partially matched"
    return "Unmatched"


def reimbursement_status_class(allocated: Decimal, remaining: Decimal) -> str:
    """Return Bootstrap badge classes for a reimbursement credit."""
    if remaining <= 0:
        return "text-bg-success"
    if allocated > 0:
        return "text-bg-warning"
    return "text-bg-info"


def expense_status_label(allocated: Decimal, remaining: Decimal, completed: bool) -> str:
    """Return the status label for a reimbursable expense."""
    if completed:
        return "Complete"
    if remaining <= 0:
        return "Fully reimbursed"
    if allocated > 0:
        return "Partially reimbursed"
    return "Awaiting reimbursement"


def expense_status_class(allocated: Decimal, remaining: Decimal, completed: bool) -> str:
    """Return Bootstrap badge classes for a reimbursable expense."""
    if completed:
        return "text-bg-success"
    if remaining <= 0:
        return "text-bg-success"
    if allocated > 0:
        return "text-bg-warning"
    return "text-bg-secondary"
