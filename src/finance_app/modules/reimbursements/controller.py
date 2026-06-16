"""Flask routes for the reimbursements feature."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from finance_app.core.i18n import gettext
from finance_app.modules.auth.permissions import PERMISSION_EDIT_TRANSACTIONS, permission_required
from finance_app.modules.reimbursements.service import (
    ReimbursementAllocationError,
    build_reimbursements_context,
    complete_reimbursable_expense,
    create_reimbursement_allocation,
    create_reimbursement_matches,
    delete_reimbursement_allocation,
    reopen_reimbursable_expense,
    update_reimbursement_allocation_amount,
)

reimbursements_bp = Blueprint("reimbursements", __name__)


@reimbursements_bp.route("/reimbursements")
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def reimbursements() -> ResponseReturnValue:
    """Render the reimbursement monitoring and allocation page."""
    return render_template("reimbursements.html", **build_reimbursements_context())


@reimbursements_bp.route("/reimbursements/allocations", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def add_allocation() -> ResponseReturnValue:
    """Create one or more reimbursement matches from submitted transaction ids."""
    try:
        if request.form.get("match_mode") == "multiple":
            results = create_reimbursement_matches(
                request.form.get("reimbursement_transaction_id"),
                selected_expense_matches(),
            )
        else:
            results = [
                create_reimbursement_allocation(
                    request.form.get("reimbursement_transaction_id"),
                    request.form.get("expense_transaction_id"),
                    request.form.get("amount", ""),
                )
            ]
    except ReimbursementAllocationError as exc:
        flash(gettext(str(exc)))
    else:
        if len(results) == 1:
            flash(gettext("Reimbursement match saved."))
        else:
            flash(gettext("{count} reimbursement matches saved.", count=len(results)))
    return redirect(reimbursements_url())


@reimbursements_bp.route("/reimbursements/allocations/<int:allocation_id>/update", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def update_allocation(allocation_id: int) -> ResponseReturnValue:
    """Update a reimbursement match amount."""
    try:
        update_reimbursement_allocation_amount(allocation_id, request.form.get("amount", ""))
    except ReimbursementAllocationError as exc:
        flash(gettext(str(exc)))
    else:
        flash(gettext("Reimbursement match updated."))
    return redirect(reimbursements_url())


@reimbursements_bp.route("/reimbursements/allocations/<int:allocation_id>/delete", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def delete_allocation(allocation_id: int) -> ResponseReturnValue:
    """Delete a reimbursement match."""
    if delete_reimbursement_allocation(allocation_id):
        flash(gettext("Reimbursement match removed."))
    else:
        flash(gettext("Reimbursement match was not found."))
    return redirect(reimbursements_url())


@reimbursements_bp.route("/reimbursements/expenses/<int:expense_transaction_id>/complete", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def complete_expense(expense_transaction_id: int) -> ResponseReturnValue:
    """Close an expense for reimbursement tracking."""
    try:
        complete_reimbursable_expense(expense_transaction_id)
    except ReimbursementAllocationError as exc:
        flash(gettext(str(exc)))
    else:
        flash(gettext("Expense closed."))
    return redirect(reimbursements_url())


@reimbursements_bp.route("/reimbursements/expenses/<int:expense_transaction_id>/reopen", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def reopen_expense(expense_transaction_id: int) -> ResponseReturnValue:
    """Reopen an expense for reimbursement matching."""
    if reopen_reimbursable_expense(expense_transaction_id):
        flash(gettext("Expense reopened for reimbursement matching."))
    else:
        flash(gettext("Expense was already open for reimbursement matching."))
    return redirect(reimbursements_url())


def reimbursements_url() -> str:
    """Return the reimbursement page URL."""
    return url_for("reimbursements.reimbursements")


def selected_expense_matches() -> list[tuple[str, str]]:
    """Return selected expense ids paired with their submitted match amounts."""
    matches = []
    for expense_id in request.form.getlist("expense_transaction_ids"):
        clean_expense_id = str(expense_id).strip()
        if clean_expense_id:
            matches.append((clean_expense_id, request.form.get(f"amount_{clean_expense_id}", "")))
    return matches
