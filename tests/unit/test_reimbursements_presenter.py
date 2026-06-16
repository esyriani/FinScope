"""Tests for reimbursement view-model shaping."""

from datetime import date
from decimal import Decimal

from finance_app.modules.reimbursements.presenter import (
    MATCH_CANDIDATE_LIMIT,
    build_reimbursements_view_model,
)


def reimbursement_row(index: int) -> dict[str, object]:
    """Return one raw reimbursement credit row."""
    return {
        "id": index,
        "tx_date": date(2026, 5, min(index, 28)),
        "description": f"Reimbursement {index}",
        "amount": Decimal("-1000.00"),
        "category": "Reimbursement",
        "transaction_kind": "income",
        "allocated": Decimal("0.00"),
    }


def expense_row(index: int) -> dict[str, object]:
    """Return one raw reimbursable expense row."""
    return {
        "id": 100 + index,
        "tx_date": date(2026, 4, min(index, 28)),
        "description": f"Expense {index}",
        "amount": Decimal("100.00"),
        "category": "Travel",
        "transaction_kind": "expense",
        "allocated": Decimal("0.00"),
    }


def test_action_needed_view_model_limits_default_working_set():
    """Verify each action row has a bounded candidate list."""
    reimbursements = [reimbursement_row(index) for index in range(1, 6)]
    expenses = [expense_row(index) for index in range(1, MATCH_CANDIDATE_LIMIT + 4)]

    view_model = build_reimbursements_view_model(reimbursements, expenses, [])
    action_needed = view_model["action_needed"]
    first_reimbursement = action_needed["reimbursements"][0]

    assert len(action_needed["reimbursements"]) == len(reimbursements)
    assert len(action_needed["expenses"]) == len(expenses)
    assert len(first_reimbursement["match_candidates"]) == MATCH_CANDIDATE_LIMIT
    assert first_reimbursement["hidden_match_candidate_count"] == len(expenses) - MATCH_CANDIDATE_LIMIT


def test_allocation_rows_include_update_maximums():
    """Verify match-history edit limits respect both linked transaction amounts."""
    allocations = [
        {
            "id": 1,
            "amount": Decimal("300.00"),
            "created_at": "2026-05-01T00:00:00Z",
            "reimbursement_transaction_id": 10,
            "expense_transaction_id": 20,
            "reimbursement_date": date(2026, 5, 1),
            "reimbursement_description": "Employer reimbursement",
            "reimbursement_amount": Decimal("-900.00"),
            "expense_date": date(2026, 4, 1),
            "expense_description": "Conference flight",
            "expense_amount": Decimal("1000.00"),
            "expense_category": "Travel",
        },
        {
            "id": 2,
            "amount": Decimal("400.00"),
            "created_at": "2026-05-02T00:00:00Z",
            "reimbursement_transaction_id": 11,
            "expense_transaction_id": 20,
            "reimbursement_date": date(2026, 5, 2),
            "reimbursement_description": "Second reimbursement",
            "reimbursement_amount": Decimal("-700.00"),
            "expense_date": date(2026, 4, 1),
            "expense_description": "Conference flight",
            "expense_amount": Decimal("1000.00"),
            "expense_category": "Travel",
        },
    ]

    view_model = build_reimbursements_view_model([], [], allocations)

    assert view_model["allocations"][0]["max_amount"] == Decimal("600.00")
    assert view_model["allocations"][1]["max_amount"] == Decimal("700.00")
