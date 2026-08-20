"""Flask routes for the upload feature."""

from typing import Any

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from finance_app.core.i18n import gettext
from finance_app.modules.auth.permissions import PERMISSION_IMPORT_STATEMENTS, permission_required
from finance_app.modules.categories.llm_token_confirmation import ai_token_estimate_confirmed
from finance_app.modules.categories.llm_token_presenter import localize_token_estimate_result
from finance_app.modules.categories.llm_tokens import AI_TOKEN_ESTIMATE_REQUIRED_MESSAGE
from finance_app.modules.upload import service as upload_service

upload_bp = Blueprint("upload", __name__)


@upload_bp.route("/upload", methods=["GET", "POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def upload() -> ResponseReturnValue:
    """Render the upload page."""
    if request.method == "POST":
        return handle_statement_upload()

    return render_template("upload.html", **upload_service.build_upload_context(request.args))


def handle_statement_upload() -> ResponseReturnValue:
    """Handle statement upload."""
    result = upload_service.queue_uploaded_statement_import(request.files.get("statement"), request.form)
    if not result["ok"]:
        flash(localized_upload_message(result))
        return redirect(url_for("upload.upload"))

    flash(
        gettext(
            "Statement queued for background import and categorization. Track progress on the Processing page. Job: {job_id}",
            job_id=result["job_id"][:8],
        )
    )
    return redirect(url_for("upload.upload"))


@upload_bp.route("/upload/preview", methods=["POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def preview_statement_upload() -> ResponseReturnValue:
    """Return a read-only parsed CSV preview for a submitted statement upload.

    The request must include the same multipart fields as the final upload. The
    response is JSON and does not create statements, transactions, accounts, or
    background jobs.
    """
    result = upload_service.build_statement_upload_preview_response(request.files.get("statement"), request.form)
    if not result["ok"]:
        return preview_error(localized_upload_message(result), int(result.get("status_code", 400)))

    return jsonify({"ok": True, "preview": result["preview"]})


def preview_error(message: str, status_code: int = 400) -> ResponseReturnValue:
    """Return a JSON error response for the upload preview endpoint."""
    return jsonify({"ok": False, "message": message}), status_code


@upload_bp.route("/upload/<int:statement_id>/retry", methods=["POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def retry_statement_import(statement_id: int) -> ResponseReturnValue:
    """Queue another import attempt for an existing stored statement."""
    return queue_existing_statement_import(statement_id, reprocess=False)


@upload_bp.route("/upload/<int:statement_id>/reprocess", methods=["POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def reprocess_statement_import(statement_id: int) -> ResponseReturnValue:
    """Queue a fresh import that replaces a statement's transactions on success."""
    return queue_existing_statement_import(statement_id, reprocess=True)


@upload_bp.route("/upload/<int:statement_id>/categorize-unknowns", methods=["POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def categorize_statement_unknowns(statement_id: int) -> ResponseReturnValue:
    """Queue AI categorization for one statement's current unknown rows."""
    next_url = upload_redirect_target()

    validation = upload_service.statement_unknown_categorization_request(statement_id)
    if not validation["ok"]:
        flash(localized_upload_message(validation))
        return redirect(next_url)

    if not ai_token_estimate_confirmed(request.form):
        flash(gettext(AI_TOKEN_ESTIMATE_REQUIRED_MESSAGE))
        return redirect(next_url)

    result = upload_service.queue_statement_unknown_categorization(statement_id)
    if not result["ok"]:
        flash(localized_upload_message(result))
        return redirect(next_url)

    unknown_count = result["unknown_count"]
    job_id = result["job_id"]
    flash(
        gettext(
            (
                "AI categorization queued for {count} unknown transaction. Track progress on the Processing page. Job: {job_id}"
                if unknown_count == 1
                else "AI categorization queued for {count} unknown transactions. Track progress on the Processing page. Job: {job_id}"
            ),
            count=unknown_count,
            job_id=job_id[:8],
        )
    )
    return redirect(next_url)


@upload_bp.route("/upload/<int:statement_id>/categorize-unknowns/estimate", methods=["POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def estimate_categorize_statement_unknowns(statement_id: int) -> ResponseReturnValue:
    """Return a token estimate for one statement's current unknown rows."""
    result = upload_service.estimate_statement_unknown_categorization(statement_id)
    status_code = 200 if result.get("ok") else int(result.get("status_code", 400))
    return jsonify(localized_json_result(result)), status_code


def queue_existing_statement_import(statement_id: int, reprocess: bool = False) -> ResponseReturnValue:
    """Queue import work from stored statement text without accepting a new file."""
    next_url = upload_redirect_target()
    result = upload_service.queue_existing_statement_import(statement_id, reprocess=reprocess)
    if not result["ok"]:
        flash(localized_upload_message(result))
        return redirect(next_url)

    flash(
        gettext(
            "{action} queued. Track progress on the Processing page. Job: {job_id}",
            action=gettext(result["action"]),
            job_id=result["job_id"][:8],
        )
    )
    return redirect(next_url)


def upload_redirect_target() -> str:
    """Return a safe redirect target for upload actions."""
    target = request.form.get("next", "").strip()
    if target.startswith("/upload"):
        return target

    return url_for("upload.upload")


def localized_json_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON result with its top-level message localized."""
    return localize_token_estimate_result(result, gettext)


def localized_upload_message(result: dict[str, Any]) -> str:
    """Return a localized upload service message with optional parameters."""
    return gettext(result["message"], **result.get("params", {}))
