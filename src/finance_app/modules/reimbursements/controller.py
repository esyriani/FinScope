"""Flask routes for the reimbursements feature."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from finance_app.core.i18n import gettext
from finance_app.modules.auth.permissions import PERMISSION_EDIT_TRANSACTIONS, permission_required
from finance_app.modules.reimbursements.service import (
    ReimbursementAllocationError,
    build_reimbursements_context_from_args,
    complete_reimbursable_expense,
    create_expense_reimbursement_matches,
    create_reimbursement_allocation,
    create_reimbursement_matches,
    delete_reimbursement_allocation,
    resume_reimbursable_expense,
    set_expense_reimbursable_tag,
    update_reimbursement_allocation_amount,
)

reimbursements_bp = Blueprint("reimbursements", __name__)


@reimbursements_bp.route("/reimbursements")
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def reimbursements() -> ResponseReturnValue:
    """Render the reimbursement monitoring and allocation page."""
    return render_template("reimbursements.html", **build_reimbursements_context_from_args(request.args))


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
        elif request.form.get("match_mode") == "expense_multiple":
            results = create_expense_reimbursement_matches(
                request.form.get("expense_transaction_id"),
                selected_reimbursement_matches(),
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
    """Mark an expense complete for reimbursement tracking."""
    try:
        complete_reimbursable_expense(expense_transaction_id)
    except ReimbursementAllocationError as exc:
        flash(gettext(str(exc)))
    else:
        flash(gettext("Expense marked complete."))
    return redirect(reimbursements_url())


@reimbursements_bp.route("/reimbursements/expenses/<int:expense_transaction_id>/resume", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def resume_expense(expense_transaction_id: int) -> ResponseReturnValue:
    """Return an expense to reimbursement tracking."""
    if resume_reimbursable_expense(expense_transaction_id):
        flash(gettext("Expense returned to reimbursement tracking."))
    else:
        flash(gettext("Expense was already in reimbursement tracking."))
    return redirect(reimbursements_url())


@reimbursements_bp.route("/reimbursements/expenses/<int:expense_transaction_id>/reimbursable-tag", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def update_expense_reimbursable_tag(expense_transaction_id: int) -> ResponseReturnValue:
    """Add or remove the Reimbursable tag on one expense transaction."""
    enabled = request.form.get("reimbursable") == "1"
    try:
        changed = set_expense_reimbursable_tag(expense_transaction_id, enabled)
    except ReimbursementAllocationError as exc:
        flash(gettext(str(exc)))
    else:
        if changed:
            flash(gettext("Reimbursable tag added." if enabled else "Reimbursable tag removed."))
        else:
            flash(gettext("Reimbursable tag was already set." if enabled else "Reimbursable tag was already removed."))
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


def selected_reimbursement_matches() -> list[tuple[str, str]]:
    """Return selected reimbursement ids paired with their submitted match amounts."""
    matches = []
    for reimbursement_id in request.form.getlist("reimbursement_transaction_ids"):
        clean_reimbursement_id = str(reimbursement_id).strip()
        if clean_reimbursement_id:
            matches.append((clean_reimbursement_id, request.form.get(f"amount_{clean_reimbursement_id}", "")))
    return matches
