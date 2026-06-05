"""Flask routes for the transactions feature."""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from finance_app.core.i18n import gettext
from finance_app.modules.categories.taxonomy import (
    get_tag_options,
    normalize_tag_names,
)
from finance_app.modules.auth.permissions import PERMISSION_EDIT_TRANSACTIONS, permission_required
from finance_app.modules.categories.service import (
    get_category_options,
    normalize_category,
)
from finance_app.database.engine import db_core_transaction
from finance_app.modules.transactions.repository import (
    assign_manual_category,
    get_transaction_for_category_update,
    mark_transaction_verified,
    set_transaction_ignored,
)
from finance_app.modules.rules.forms import amount_bounds_label, normalize_rule_keyword, parse_amount_bounds
from finance_app.modules.transactions import service as transactions_service
from finance_app.modules.transactions.urls import transactions_redirect_target, transactions_redirect_with_ignored

transactions_bp = Blueprint("transactions", __name__)


@transactions_bp.route("/transactions")
def transactions():
    """Render the transactions page."""
    context = transactions_service.build_transactions_context(request.args)
    context["transaction_ai_result"] = session.pop("transaction_ai_result", None)
    return render_template("transactions.html", **context)


@transactions_bp.route("/transactions/<int:transaction_id>/category", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def update_transaction_category(transaction_id):
    """Apply a manual category update to one transaction."""
    next_url = transactions_redirect_target()
    with db_core_transaction() as conn:
        category_options = get_category_options(conn)
        tag_options = get_tag_options(conn)
        new_category = normalize_category(request.form.get("category", ""), category_options)
        tag_names = normalize_tag_names(request.form.getlist("tags"), tag_options)

        if not new_category:
            flash(gettext("Category cannot be empty."))
            return redirect(next_url or url_for("transactions.transactions"))

        tx = get_transaction_for_category_update(conn, transaction_id)

        if tx is None:
            flash(gettext("Transaction not found."))
            return redirect(next_url or url_for("transactions.transactions"))

        description = tx["description"].strip()
        rule_action = request.form.get("rule_action", "transaction_only")
        merchant_key = ""
        amount_min = None
        amount_max = None

        if rule_action == "save":
            merchant_key = normalize_rule_keyword(request.form.get("keyword", ""), description)
            try:
                amount_min, amount_max = parse_amount_bounds(
                    request.form.get("amount_min", ""),
                    request.form.get("amount_max", ""),
            )
            except ValueError as exc:
                flash(gettext(str(exc)))
                return redirect(next_url or url_for("transactions.transactions"))

            if not merchant_key:
                flash(gettext("Rule keyword is required when saving a rule."))
                return redirect(next_url or url_for("transactions.transactions"))

        result = assign_manual_category(
            conn,
            transaction_id,
            new_category,
            tag_names=tag_names,
            rule_keyword=merchant_key if rule_action == "save" else None,
            amount_min=amount_min,
            amount_max=amount_max,
            rule_merchant_id=tx["merchant_id"] if rule_action == "save" else None,
        )

        if not result.updated:
            flash(gettext("Transaction not found."))
            return redirect(next_url or url_for("transactions.transactions"))

        if rule_action == "save":
            flash(
                gettext(
                    (
                        "Category updated. Rule saved for: {keyword}{amount_bounds}"
                        if result.transaction_changed
                        else "Rule saved for: {keyword}{amount_bounds}"
                    ),
                    keyword=merchant_key,
                    amount_bounds=amount_bounds_label(amount_min, amount_max),
                )
            )
        elif result.transaction_changed:
            flash(gettext("Category updated for this transaction only."))
        else:
            flash(gettext("No transaction changes to save."))
    return redirect(next_url or url_for("transactions.transactions"))


@transactions_bp.route("/transactions/<int:transaction_id>/verify", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def verify_transaction(transaction_id):
    """Mark one transaction as manually verified."""
    next_url = transactions_redirect_target()
    with db_core_transaction() as conn:
        updated = mark_transaction_verified(conn, transaction_id)

    flash(gettext("Transaction approved." if updated else "Transaction not found."))
    return redirect(next_url or url_for("transactions.transactions"))


@transactions_bp.route("/transactions/<int:transaction_id>/ignored", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def update_transaction_ignored(transaction_id):
    """Update the ignored flag for one transaction."""
    next_url = transactions_redirect_target()
    ignored = 1 if request.form.get("ignored") == "1" else 0
    with db_core_transaction() as conn:
        updated = set_transaction_ignored(conn, transaction_id, ignored)

    if updated:
        flash(gettext("Transaction ignored." if ignored else "Transaction restored."))
    else:
        flash(gettext("Transaction not found."))
    return redirect(transactions_redirect_with_ignored(next_url, "all"))


@transactions_bp.route("/transactions/batch", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def batch_transactions():
    """Apply one batch action to explicitly selected transaction IDs."""
    next_url = transactions_redirect_target()
    transaction_ids = request.form.getlist("transaction_ids")
    action = request.form.get("batch_action", "").strip()
    redirect_url = next_url or url_for("transactions.transactions")

    if not transaction_ids:
        flash(gettext("Select at least one transaction."))
        return redirect(redirect_url)

    if action == "approve":
        updated = transactions_service.approve_selected_transactions(transaction_ids)
        flash(
            gettext(
                "Approved selected transaction."
                if updated == 1
                else "Approved selected transactions."
            )
        )
        return redirect(redirect_url)

    if action == "ignore":
        updated = transactions_service.ignore_selected_transactions(transaction_ids)
        flash(
            gettext(
                "Ignored selected transaction."
                if updated == 1
                else "Ignored selected transactions."
            )
        )
        return redirect(transactions_redirect_with_ignored(redirect_url, "all"))

    if action == "recategorize":
        result = transactions_service.queue_selected_transaction_recategorization(transaction_ids)
        job_id = result.get("job_id")
        selected_count = result.get("selected_count") or 0
        if job_id:
            flash(
                gettext(
                    (
                        "Recategorization queued for {count} selected transaction. Job: {job_id}"
                        if selected_count == 1
                        else "Recategorization queued for {count} selected transactions. Job: {job_id}"
                    ),
                    count=selected_count,
                    job_id=job_id[:8],
                )
            )
        else:
            flash(gettext("Select at least one transaction."))
        return redirect(redirect_url)

    flash(gettext("Choose a batch action."))
    return redirect(redirect_url)


@transactions_bp.route("/transactions/<int:transaction_id>/run-ai", methods=["POST"])
@transactions_bp.route("/transactions/<int:transaction_id>/suggest-category", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def suggest_transaction_category(transaction_id):
    """Preview an AI category suggestion for one transaction.

    The route stores the signed-session suggestion for an explicit follow-up
    apply action. It does not mutate the selected transaction or create a rule.
    """
    next_url = transactions_redirect_target()
    result = transactions_service.suggest_transaction_ai_category(transaction_id)
    display_result = dict(result)
    persistence = display_result.pop("persistence", None)
    session["transaction_ai_result"] = display_result
    if result.get("can_apply"):
        session["transaction_ai_suggestion"] = {
            "transaction_id": result.get("transaction_id"),
            "can_apply": True,
            "persistence": persistence,
        }
    else:
        session.pop("transaction_ai_suggestion", None)
    flash(gettext(result.get("message") or "AI categorization completed."))
    return redirect(next_url or url_for("transactions.transactions"))


@transactions_bp.route("/transactions/<int:transaction_id>/ai-suggestion", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def apply_transaction_ai_suggestion(transaction_id):
    """Apply a pending AI suggestion and optionally save a category rule."""
    next_url = transactions_redirect_target()
    action = request.form.get(
        "suggestion_action",
        transactions_service.APPLY_AI_SUGGESTION_ACTION,
    )
    amount_min = None
    amount_max = None
    if action == transactions_service.APPLY_AI_SUGGESTION_WITH_RULE_ACTION:
        try:
            amount_min, amount_max = parse_amount_bounds(
                request.form.get("amount_min", ""),
                request.form.get("amount_max", ""),
        )
        except ValueError as exc:
            flash(gettext(str(exc)))
            return redirect(next_url or url_for("transactions.transactions"))

    result = transactions_service.apply_transaction_ai_suggestion(
        transaction_id,
        session.get("transaction_ai_suggestion"),
        action=action,
        rule_keyword=request.form.get("keyword", ""),
        amount_min=amount_min,
        amount_max=amount_max,
    )
    if result.get("updated"):
        session.pop("transaction_ai_suggestion", None)
    flash(gettext(result.get("message") or "AI suggestion cannot be applied."))
    return redirect(next_url or url_for("transactions.transactions"))

