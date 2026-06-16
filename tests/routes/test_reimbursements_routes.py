"""Route tests for reimbursement monitoring and allocation flows."""

from decimal import Decimal

from sqlalchemy import select
from tests.support.html import (
    assert_form,
    assert_has_element,
    assert_no_element,
    assert_not_visible_text,
    assert_visible_text,
)

from finance_app.core.constants import (
    REIMBURSEMENT_CATEGORY,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
)
from finance_app.database.tables import reimbursement_allocations as reimbursement_allocations_table
from finance_app.database.tables import reimbursement_expense_completions as expense_completions_table
from finance_app.modules.reimbursements.service import create_reimbursement_allocation


def expense_transaction(data_factory, *, amount="1000.00", transaction_kind=TRANSACTION_KIND_EXPENSE):
    """Create a positive reimbursable expense transaction."""
    return data_factory.transactions.create(
        description="Conference expense",
        amount=Decimal(amount),
        category="Travel",
        transaction_kind=transaction_kind,
        needs_review=0,
        tags=["Conference", "Reimbursable"],
    )


def reimbursement_transaction(data_factory, *, amount="-900.00", category=REIMBURSEMENT_CATEGORY):
    """Create an incoming reimbursement credit transaction."""
    return data_factory.transactions.create(
        description="Employer reimbursement",
        amount=Decimal(amount),
        category=category,
        transaction_kind=TRANSACTION_KIND_INCOME,
        needs_review=0,
    )


def allocation_rows(conn):
    """Return persisted reimbursement allocation rows."""
    return conn.execute(select(reimbursement_allocations_table)).mappings().fetchall()


def completion_rows(conn):
    """Return persisted reimbursement expense completion rows."""
    return conn.execute(select(expense_completions_table)).mappings().fetchall()


def test_reimbursements_page_lists_open_credits_and_expenses(csrf_client, data_factory):
    """Verify the reimbursement page renders available credits and expenses."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")

    response = csrf_client.get("/reimbursements")

    assert response.status_code == 200
    assert_form(response, "/reimbursements/allocations", method="post")
    assert_has_element(response, "button", attrs={"id": "reimbursements-action-tab", "class": "active"})
    assert_has_element(response, "section", attrs={"id": "reimbursements-action-panel", "class": "active"})
    assert_has_element(
        response,
        "table",
        attrs={
            "class": "reimbursement-action-table",
            "data-sortable-table": True,
            "data-paginated-table": True,
            "data-page-size": True,
        },
    )
    assert_has_element(
        response,
        "table",
        attrs={
            "class": "reimbursement-action-expense-table",
            "data-sortable-table": True,
            "data-paginated-table": True,
            "data-page-size": True,
        },
    )
    assert_has_element(
        response,
        "table",
        attrs={
            "class": "reimbursement-received-table",
            "data-sortable-table": True,
            "data-paginated-table": True,
            "data-page-size": True,
        },
    )
    assert_has_element(
        response,
        "tr",
        attrs={"data-row-edit-target": f"#match-reimbursement-{reimbursement_id}-modal"},
    )
    assert_has_element(response, "tr", attrs={"id": f"action-expense-{expense_id}"})
    assert_has_element(
        response,
        "div",
        attrs={"class": "progress", "role": "progressbar", "aria-valuenow": "0"},
    )
    assert_has_element(
        response, "form", attrs={"action": "/reimbursements/allocations", "data-ajax-refresh-form": True}
    )
    assert_has_element(
        response,
        "button",
        attrs={"class": "btn-outline-secondary", "data-bs-target": f"#match-reimbursement-{reimbursement_id}-modal"},
        text="Match",
    )
    assert_visible_text(
        response,
        "Reimbursements",
        "Action needed",
        "Active reimbursements",
        "Active expenses",
        "Match reimbursement",
        "Candidate expenses",
        "Employer reimbursement",
        "Conference expense",
        "Unmatched",
        "Awaiting reimbursement",
    )
    assert_not_visible_text(response, "Create allocation", "Open credits", "Pending expenses")


def test_reimbursements_allocation_post_persists_link(csrf_client, core_conn, data_factory):
    """Verify match form submissions create reimbursement links."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")

    response = csrf_client.post(
        "/reimbursements/allocations",
        data={
            "reimbursement_transaction_id": reimbursement_id,
            "expense_transaction_id": expense_id,
            "amount": "900.00",
        },
        follow_redirects=True,
    )

    rows = allocation_rows(core_conn)
    assert response.status_code == 200
    assert len(rows) == 1
    assert rows[0]["reimbursement_transaction_id"] == reimbursement_id
    assert rows[0]["expense_transaction_id"] == expense_id
    assert rows[0]["amount"] == Decimal("900.00")
    assert_visible_text(response, "Reimbursement match saved.", "Partially reimbursed")


def test_reimbursements_multiple_match_post_persists_links(csrf_client, core_conn, data_factory):
    """Verify one reimbursement can be matched to several selected expenses."""
    first_expense_id = expense_transaction(data_factory, amount="500.00")
    second_expense_id = expense_transaction(data_factory, amount="400.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")

    response = csrf_client.post(
        "/reimbursements/allocations",
        data={
            "match_mode": "multiple",
            "reimbursement_transaction_id": reimbursement_id,
            "expense_transaction_ids": [str(first_expense_id), str(second_expense_id)],
            f"amount_{first_expense_id}": "500.00",
            f"amount_{second_expense_id}": "400.00",
        },
        follow_redirects=True,
    )

    rows = allocation_rows(core_conn)
    assert response.status_code == 200
    assert len(rows) == 2
    assert {row["expense_transaction_id"] for row in rows} == {first_expense_id, second_expense_id}
    assert sum((row["amount"] for row in rows), Decimal("0.00")) == Decimal("900.00")
    assert_visible_text(response, "2 reimbursement matches saved.", "Fully matched")


def test_reimbursements_allocation_post_flashes_validation_errors(csrf_client, core_conn, data_factory):
    """Verify invalid allocation submissions show domain validation errors."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    income_id = reimbursement_transaction(data_factory, amount="-900.00", category="Income")

    response = csrf_client.post(
        "/reimbursements/allocations",
        data={
            "reimbursement_transaction_id": income_id,
            "expense_transaction_id": expense_id,
            "amount": "100.00",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert allocation_rows(core_conn) == []
    assert_visible_text(response, "Reimbursement transaction must use the Reimbursement category.")


def test_reimbursements_update_and_delete_allocation_routes(csrf_client, core_conn, data_factory):
    """Verify match update and delete forms persist changes."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")
    allocation = create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("300.00"), conn=core_conn)

    update_response = csrf_client.post(
        f"/reimbursements/allocations/{allocation.id}/update",
        data={"amount": "400.00"},
        follow_redirects=True,
    )
    rows = allocation_rows(core_conn)

    delete_response = csrf_client.post(
        f"/reimbursements/allocations/{allocation.id}/delete",
        follow_redirects=True,
    )

    assert update_response.status_code == 200
    assert rows[0]["amount"] == Decimal("400.00")
    assert_visible_text(update_response, "Reimbursement match updated.")
    assert_has_element(
        update_response,
        "input",
        attrs={"id": f"match-{allocation.id}-amount", "max": "900.00", "value": "400.00"},
    )
    assert_has_element(
        update_response,
        "form",
        attrs={
            "action": f"/reimbursements/allocations/{allocation.id}/update",
            "data-ajax-refresh-form": True,
        },
    )
    assert delete_response.status_code == 200
    assert allocation_rows(core_conn) == []
    assert_visible_text(delete_response, "Reimbursement match removed.")


def test_reimbursements_expense_completion_routes_close_and_reopen_expense(csrf_client, core_conn, data_factory):
    """Verify policy-limited reimbursable expenses can be closed and reopened."""
    expense_id = expense_transaction(data_factory, amount="1000.00")
    reimbursement_id = reimbursement_transaction(data_factory, amount="-900.00")
    create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("900.00"), conn=core_conn)

    complete_response = csrf_client.post(
        f"/reimbursements/expenses/{expense_id}/complete",
        follow_redirects=True,
    )
    completed_rows = completion_rows(core_conn)

    reopen_response = csrf_client.post(
        f"/reimbursements/expenses/{expense_id}/reopen",
        follow_redirects=True,
    )

    assert complete_response.status_code == 200
    assert len(completed_rows) == 1
    assert completed_rows[0]["expense_transaction_id"] == expense_id
    assert_visible_text(complete_response, "Expense closed.", "Complete", "Reopen")
    assert_no_element(
        complete_response,
        "form",
        attrs={"action": f"/reimbursements/expenses/{expense_id}/complete"},
    )
    assert_no_element(complete_response, "tr", attrs={"id": f"action-expense-{expense_id}"})
    assert_has_element(
        complete_response,
        "form",
        attrs={
            "action": f"/reimbursements/expenses/{expense_id}/reopen",
            "data-ajax-refresh-form": True,
        },
    )
    assert reopen_response.status_code == 200
    assert completion_rows(core_conn) == []
    assert_visible_text(reopen_response, "Expense reopened for reimbursement matching.", "Partially reimbursed")
    assert_has_element(reopen_response, "tr", attrs={"id": f"action-expense-{expense_id}"})
    assert_has_element(
        reopen_response,
        "form",
        attrs={
            "action": f"/reimbursements/expenses/{expense_id}/complete",
            "data-ajax-refresh-form": True,
        },
    )


def test_reimbursements_page_requires_transaction_edit_permission(viewer_client):
    """Verify viewers cannot access reimbursement monitoring."""
    response = viewer_client.get("/reimbursements")

    assert response.status_code == 403
