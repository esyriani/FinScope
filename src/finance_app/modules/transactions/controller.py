"""Flask routes for the transactions feature."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from finance_app.modules.categories.taxonomy import (
    get_tag_options,
    normalize_tag_names,
)
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
from finance_app.modules.transactions.service import build_transactions_context
from finance_app.modules.transactions.urls import transactions_redirect_target, transactions_redirect_with_ignored

transactions_bp = Blueprint("transactions", __name__)


@transactions_bp.route("/transactions")
def transactions():
    """Render the transactions page."""
    return render_template("transactions.html", **build_transactions_context(request.args))


@transactions_bp.route("/transactions/<int:transaction_id>/category", methods=["POST"])
def update_transaction_category(transaction_id):
    """Apply a manual category update to one transaction."""
    next_url = transactions_redirect_target()
    with db_core_transaction() as conn:
        category_options = get_category_options(conn)
        tag_options = get_tag_options(conn)
        new_category = normalize_category(request.form.get("category", ""), category_options)
        tag_names = normalize_tag_names(request.form.getlist("tags"), tag_options)

        if not new_category:
            flash("Category cannot be empty.")
            return redirect(next_url or url_for("transactions.transactions"))

        tx = get_transaction_for_category_update(conn, transaction_id)

        if tx is None:
            flash("Transaction not found.")
            return redirect(next_url or url_for("transactions.transactions"))

        description = tx["description"].strip()
        rule_action = request.form.get("rule_action", "save")
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
                flash(str(exc))
                return redirect(next_url or url_for("transactions.transactions"))

            if not merchant_key:
                flash("Rule keyword is required when saving a rule.")
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
            flash("Transaction not found.")
            return redirect(next_url or url_for("transactions.transactions"))

        if rule_action == "save":
            flash(
                f"Category updated. Rule saved for: "
                f"{merchant_key}{amount_bounds_label(amount_min, amount_max)}"
            )
        else:
            flash("Category updated for this transaction only.")
    return redirect(next_url or url_for("transactions.transactions"))


@transactions_bp.route("/transactions/<int:transaction_id>/verify", methods=["POST"])
def verify_transaction(transaction_id):
    """Mark one transaction as manually verified."""
    next_url = transactions_redirect_target()
    with db_core_transaction() as conn:
        updated = mark_transaction_verified(conn, transaction_id)

    flash("Transaction marked verified." if updated else "Transaction not found.")
    return redirect(next_url or url_for("transactions.transactions"))


@transactions_bp.route("/transactions/<int:transaction_id>/ignored", methods=["POST"])
def update_transaction_ignored(transaction_id):
    """Update the ignored flag for one transaction."""
    next_url = transactions_redirect_target()
    ignored = 1 if request.form.get("ignored") == "1" else 0
    with db_core_transaction() as conn:
        updated = set_transaction_ignored(conn, transaction_id, ignored)

    if updated:
        flash("Transaction ignored." if ignored else "Transaction restored.")
    else:
        flash("Transaction not found.")
    return redirect(transactions_redirect_with_ignored(next_url, "all"))

