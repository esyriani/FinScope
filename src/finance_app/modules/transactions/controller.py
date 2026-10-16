"""Flask routes for the transactions feature."""

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user  # type: ignore[import-untyped]

from finance_app.core.i18n import gettext
from finance_app.modules.auth.permissions import PERMISSION_EDIT_TRANSACTIONS, permission_required
from finance_app.modules.categories.llm_token_confirmation import ai_token_estimate_confirmed
from finance_app.modules.categories.llm_token_presenter import localize_token_estimate_result
from finance_app.modules.categories.llm_tokens import AI_TOKEN_ESTIMATE_REQUIRED_MESSAGE
from finance_app.modules.rules.forms import parse_amount_bounds
from finance_app.modules.transactions import backup_workflow
from finance_app.modules.transactions import service as transactions_service
from finance_app.modules.transactions.ai_payloads import (
    get_transaction_ai_suggestion,
    pop_transaction_ai_result,
    pop_transaction_ai_suggestion,
    store_transaction_ai_result,
    store_transaction_ai_suggestion,
)
from finance_app.modules.transactions.backup_payloads import (
    store_transaction_backup_import,
    transaction_backup_import_payload_store,
)
from finance_app.modules.transactions.urls import transactions_redirect_target, transactions_redirect_with_ignored

transactions_bp = Blueprint("transactions", __name__)
LEGACY_TRANSACTION_AI_RESULT = "transaction_ai_result"
LEGACY_TRANSACTION_AI_SUGGESTION = "transaction_ai_suggestion"
TRANSACTION_AI_RESULT_REFERENCE = "transaction_ai_result_reference"
TRANSACTION_AI_SUGGESTION_REFERENCE = "transaction_ai_suggestion_reference"
TRANSACTION_BACKUP_IMPORT_REFERENCE = "transaction_backup_import_reference"
TRANSACTION_BACKUP_IMPORT_JOB_ID = "transaction_backup_import_job_id"


@transactions_bp.route("/transactions")
def transactions() -> str:
    """Render the transactions page."""
    context = transactions_service.build_transactions_context(request.args)
    result_reference = session.pop(TRANSACTION_AI_RESULT_REFERENCE, None)
    session.pop(LEGACY_TRANSACTION_AI_RESULT, None)
    session.pop(LEGACY_TRANSACTION_AI_SUGGESTION, None)
    context["transaction_ai_result"] = pop_transaction_ai_result(result_reference)
    backup_reference = session.pop(TRANSACTION_BACKUP_IMPORT_REFERENCE, None)
    if backup_reference:
        preview = backup_workflow.pending_transactions_backup_preview(backup_reference, str(current_user.get_id()))
        if preview is None:
            flash(gettext("Transaction backup import preview expired. Upload the backup again."))
        else:
            context["transaction_backup_import_preview"] = preview
            context["transaction_backup_import_reference"] = backup_reference
    context["transaction_backup_import_job_id"] = session.pop(TRANSACTION_BACKUP_IMPORT_JOB_ID, None)
    return render_template("transactions.html", **context)


@transactions_bp.route("/transactions/backup.csv")
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def export_transactions_backup() -> ResponseReturnValue:
    """Download a canonical CSV backup of all persisted transactions."""
    output = transactions_service.export_transactions_backup_csv()
    return Response(
        output,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=finscope-transactions-backup.csv",
        },
    )


@transactions_bp.route("/transactions/backup/preview", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def preview_transactions_backup_import() -> ResponseReturnValue:
    """Validate an editor's upload and redirect to the filtered list with a pending preview."""
    next_url = transactions_redirect_target()
    uploaded_file = request.files.get("backup_file")
    if uploaded_file is None or not uploaded_file.filename:
        flash(gettext("Choose a backup CSV file to import."))
        return redirect(next_url)

    try:
        csv_text = uploaded_file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash(gettext("The backup CSV could not be read as UTF-8 text."))
        return redirect(next_url)

    try:
        preview = transactions_service.preview_transactions_backup_import(csv_text)
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url)

    if preview.valid_row_count == 0:
        flash(gettext("No importable transaction rows were found."))
        for error in preview.row_errors[:5]:
            flash(error)
        return redirect(next_url)

    user_id = str(current_user.get_id())
    transaction_backup_import_payload_store().discard(session.pop(TRANSACTION_BACKUP_IMPORT_REFERENCE, None), user_id)
    session[TRANSACTION_BACKUP_IMPORT_REFERENCE] = store_transaction_backup_import(csv_text, user_id)
    return redirect(next_url)


@transactions_bp.route("/transactions/backup/import", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def confirm_transactions_backup_import() -> ResponseReturnValue:
    """Confirm a user-owned backup, queuing nonempty restores with CSRF and editor permission."""
    next_url = transactions_redirect_target()
    result = backup_workflow.queue_transactions_backup_import(
        request.form.get("import_reference"), str(current_user.get_id()), request.form.get("duplicate_action")
    )
    if result.get("ok") and result.get("job_id"):
        session[TRANSACTION_BACKUP_IMPORT_JOB_ID] = result["job_id"]
    elif result.get("retry_reference"):
        session[TRANSACTION_BACKUP_IMPORT_REFERENCE] = result["retry_reference"]
    flash(gettext(result["message"], **result.get("params", {})))
    return redirect(next_url)


@transactions_bp.route("/transactions/<int:transaction_id>/category", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def update_transaction_category(transaction_id: int) -> ResponseReturnValue:
    """Apply a manual category update to one transaction."""
    next_url = transactions_redirect_target()
    try:
        result = transactions_service.update_transaction_category_from_form(transaction_id, request.form)
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url or url_for("transactions.transactions"))

    flash(gettext(result["message"], **result.get("params", {})))
    return redirect(next_url or url_for("transactions.transactions"))


@transactions_bp.route("/transactions/<int:transaction_id>/verify", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def verify_transaction(transaction_id: int) -> ResponseReturnValue:
    """Mark one transaction as manually verified."""
    next_url = transactions_redirect_target()
    updated = transactions_service.verify_transaction_by_id(transaction_id)

    flash(gettext("Transaction approved." if updated else "Transaction not found."))
    return redirect(next_url or url_for("transactions.transactions"))


@transactions_bp.route("/transactions/<int:transaction_id>/review", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def mark_transaction_for_review(transaction_id: int) -> ResponseReturnValue:
    """Reopen one active transaction after CSRF validation and return to its filtered list."""
    updated = transactions_service.reopen_selected_transactions_for_review([transaction_id])
    flash(gettext("Transaction marked for review." if updated else "Active transaction not found."))
    return redirect(transactions_redirect_target() or url_for("transactions.transactions"))


@transactions_bp.route("/transactions/<int:transaction_id>/ignored", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def update_transaction_ignored(transaction_id: int) -> ResponseReturnValue:
    """Update the ignored flag for one transaction."""
    next_url = transactions_redirect_target()
    ignored = 1 if request.form.get("ignored") == "1" else 0
    updated = transactions_service.set_transaction_ignored_by_id(transaction_id, ignored)

    if updated:
        flash(gettext("Transaction ignored." if ignored else "Transaction restored."))
    else:
        flash(gettext("Transaction not found."))
    return redirect(transactions_redirect_with_ignored(next_url, "all"))


@transactions_bp.route("/transactions/batch", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def batch_transactions() -> ResponseReturnValue:
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
        flash(gettext("Approved selected transaction." if updated == 1 else "Approved selected transactions."))
        return redirect(redirect_url)

    if action == "mark_for_review":
        updated = transactions_service.reopen_selected_transactions_for_review(transaction_ids)
        flash(
            gettext(
                "Selected transaction marked for review."
                if updated == 1
                else "Selected transactions marked for review."
            )
        )
        return redirect(redirect_url)

    if action == "ignore":
        updated = transactions_service.ignore_selected_transactions(transaction_ids)
        flash(gettext("Ignored selected transaction." if updated == 1 else "Ignored selected transactions."))
        return redirect(transactions_redirect_with_ignored(redirect_url, "all"))

    if action == "recategorize":
        if not ai_token_estimate_confirmed(request.form):
            flash(gettext(AI_TOKEN_ESTIMATE_REQUIRED_MESSAGE))
            return redirect(redirect_url)

        result = transactions_service.queue_selected_transaction_recategorization(transaction_ids)
        job_id = result.get("job_id")
        selected_count = result.get("selected_count") or 0
        if result.get("ok") and job_id:
            flash(
                gettext(
                    (
                        "Recategorization queued for {count} selected transaction. Processing item: {job_id}"
                        if selected_count == 1
                        else "Recategorization queued for {count} selected transactions. Processing item: {job_id}"
                    ),
                    count=selected_count,
                    job_id=job_id[:8],
                )
            )
        else:
            flash(gettext(str(result.get("message") or "Select at least one transaction.")))
        return redirect(redirect_url)

    flash(gettext("Choose a batch action."))
    return redirect(redirect_url)


@transactions_bp.route("/transactions/batch/ai-estimate", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def estimate_batch_transaction_ai() -> ResponseReturnValue:
    """Return a token estimate for selected transaction recategorization."""
    result = transactions_service.estimate_selected_transaction_recategorization(
        request.form.getlist("transaction_ids")
    )
    status_code = 200 if result.get("ok") else 400
    return jsonify(localized_json_result(result)), status_code


@transactions_bp.route("/transactions/<int:transaction_id>/suggest-category/estimate", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def estimate_transaction_category_suggestion(transaction_id: int) -> ResponseReturnValue:
    """Return a token estimate for one transaction AI category suggestion."""
    result = transactions_service.estimate_transaction_ai_category(transaction_id)
    status_code = 200 if result.get("ok") else 400
    return jsonify(localized_json_result(result)), status_code


@transactions_bp.route("/transactions/<int:transaction_id>/run-ai", methods=["POST"])
@transactions_bp.route("/transactions/<int:transaction_id>/suggest-category", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def suggest_transaction_category(transaction_id: int) -> ResponseReturnValue:
    """Preview an AI category suggestion for one transaction.

    The route stores server-side payload references for an explicit follow-up
    apply action. It does not mutate the selected transaction or create a rule.
    """
    next_url = transactions_redirect_target()
    if not ai_token_estimate_confirmed(request.form):
        flash(gettext(AI_TOKEN_ESTIMATE_REQUIRED_MESSAGE))
        return redirect(next_url or url_for("transactions.transactions"))

    result = transactions_service.suggest_transaction_ai_category(transaction_id)
    display_result = dict(result)
    persistence = display_result.pop("persistence", None)
    original_state = display_result.pop("original_state", None)
    discard_transaction_ai_payload_references()
    session[TRANSACTION_AI_RESULT_REFERENCE] = store_transaction_ai_result(display_result)
    if result.get("can_apply"):
        session[TRANSACTION_AI_SUGGESTION_REFERENCE] = store_transaction_ai_suggestion(
            {
                "transaction_id": result.get("transaction_id"),
                "can_apply": True,
                "persistence": persistence,
                "original_state": original_state,
            }
        )
    flash(gettext(result.get("message") or "AI categorization completed."))
    return redirect(next_url or url_for("transactions.transactions"))


@transactions_bp.route("/transactions/<int:transaction_id>/ai-suggestion", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def apply_transaction_ai_suggestion(transaction_id: int) -> ResponseReturnValue:
    """Apply a pending AI suggestion and optionally save a category rule."""
    next_url = transactions_redirect_target()
    session.pop(LEGACY_TRANSACTION_AI_SUGGESTION, None)
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

    suggestion_reference = session.get(TRANSACTION_AI_SUGGESTION_REFERENCE)
    result = transactions_service.apply_transaction_ai_suggestion(
        transaction_id,
        get_transaction_ai_suggestion(suggestion_reference),
        action=action,
        rule_keyword=request.form.get("keyword", ""),
        amount_min=amount_min,
        amount_max=amount_max,
    )
    if result.get("updated") or result.get("expired"):
        pop_transaction_ai_suggestion(session.pop(TRANSACTION_AI_SUGGESTION_REFERENCE, None))
    flash(gettext(result.get("message") or "AI suggestion cannot be applied."))
    return redirect(next_url or url_for("transactions.transactions"))


def discard_transaction_ai_payload_references() -> None:
    """Discard pending transaction AI payload references from the browser session."""
    pop_transaction_ai_result(session.pop(TRANSACTION_AI_RESULT_REFERENCE, None))
    pop_transaction_ai_suggestion(session.pop(TRANSACTION_AI_SUGGESTION_REFERENCE, None))
    session.pop(LEGACY_TRANSACTION_AI_RESULT, None)
    session.pop(LEGACY_TRANSACTION_AI_SUGGESTION, None)


def localized_json_result(result: dict[str, object]) -> dict[str, object]:
    """Return a JSON result with its top-level message localized."""
    return localize_token_estimate_result(result, gettext)
