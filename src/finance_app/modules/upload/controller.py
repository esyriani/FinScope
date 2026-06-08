"""Flask routes for the upload feature."""

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from finance_app.background.runner import submit_background_job
from finance_app.core.config import settings
from finance_app.core.constants import (
    ACCOUNT_TYPE_CREDIT_CARD,
    ACTIVE_STATEMENT_IMPORT_STATUSES,
    DATE_ORDER_AUTO,
    INTERAC_DIRECTION_AUTO,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
)
from finance_app.core.i18n import gettext
from finance_app.database.engine import db_core_transaction
from finance_app.modules.accounts.repository import get_or_create_account, normalize_account_type
from finance_app.modules.auth.permissions import PERMISSION_IMPORT_STATEMENTS, permission_required
from finance_app.modules.categories.service import categorize_transactions
from finance_app.modules.categories.taxonomy import set_transaction_tags
from finance_app.modules.settings.runtime import get_statement_type_by_id
from finance_app.modules.statements.importer import (
    allowed_statement_file,
    file_checksum,
    get_file_extension,
    normalize_date_order,
    normalize_interac_direction,
)
from finance_app.modules.upload import workflow as upload_workflow
from finance_app.modules.upload.repository import (
    create_uploaded_statement,
    delete_statement_transactions,
    statement_by_checksum,
    statement_extension,
    statement_import_row,
)
from finance_app.modules.upload.service import build_statement_preview, build_upload_context

upload_bp = Blueprint("upload", __name__)


@upload_bp.route("/upload", methods=["GET", "POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def upload():
    """Render the upload page."""
    if request.method == "POST":
        return handle_statement_upload()

    return render_template("upload.html", **build_upload_context(request.args))


def handle_statement_upload():
    """Handle statement upload."""
    uploaded_file = request.files.get("statement")
    account_name = request.form.get("account_name", "Personal").strip() or "Personal"
    paid_from_account_name = request.form.get("paid_from_account_name", "").strip()
    statement_type_id = request.form.get("statement_type_id", "").strip()
    date_order = normalize_date_order(request.form.get("date_order"))

    with db_core_transaction() as conn:
        statement_type = get_statement_type_by_id(conn, statement_type_id)

        if statement_type is None:
            flash(gettext("Please choose a valid statement type."))
            return redirect(url_for("upload.upload"))

        interac_direction = INTERAC_DIRECTION_AUTO
        if statement_type["parser_type"] == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
            interac_direction = normalize_interac_direction(request.form.get("interac_direction"))

        if uploaded_file is None or uploaded_file.filename == "":
            flash(gettext("Please choose a statement file."))
            return redirect(url_for("upload.upload"))

        if not allowed_statement_file(uploaded_file.filename):
            allowed = ", ".join(sorted(settings.allowed_statement_extensions)).upper()
            flash(gettext("Only {allowed} files are supported.", allowed=allowed))
            return redirect(url_for("upload.upload"))

        checksum = file_checksum(uploaded_file)
        existing = statement_by_checksum(conn, checksum)

        if existing:
            flash(
                gettext(
                    (
                        "This statement was already uploaded as {filename} on {uploaded_at} "
                        "({status}). Use Retry import or Reprocess from Uploaded statements."
                    ),
                    filename=existing["filename"],
                    uploaded_at=existing["uploaded_at"],
                    status=existing["import_status"],
                )
            )
            return redirect(url_for("upload.upload"))

        extension = get_file_extension(uploaded_file.filename)
        raw_text = read_statement_text(uploaded_file, extension)

        if raw_text is None:
            return redirect(url_for("upload.upload"))

        preview = build_statement_preview(
            raw_text,
            statement_type["parser_type"],
            interac_direction=interac_direction,
            date_order=date_order,
        )
        if preview["date_format"]["requires_choice"]:
            flash(gettext("Choose a statement date format before uploading."))
            return redirect(url_for("upload.upload"))

        # The statement import type controls parsing/import mode. The account
        # reporting role is stored separately so reports can classify card payments
        # and transfers without changing how the statement file is parsed.
        account_type = normalize_account_type(
            request.form.get("account_type") or statement_type["default_account_type"]
        )
        paid_from_value = (paid_from_account_name or None) if account_type == ACCOUNT_TYPE_CREDIT_CARD else ""
        account = get_or_create_account(
            conn,
            account_name,
            account_type=account_type,
            paid_from_account_name=paid_from_value,
        )
        statement_id = create_uploaded_statement(
            conn,
            account["id"],
            statement_type["id"],
            uploaded_file.filename,
            checksum,
            extension,
            interac_direction,
            date_order,
            raw_text,
        )
        upload_workflow.reset_statement_import_state(conn, statement_id)

    job_id = submit_statement_import_job(
        statement_id,
        account["id"],
        statement_type["parser_type"],
        statement_type["import_mode"],
        extension,
        raw_text,
        uploaded_file.filename,
        interac_direction=interac_direction,
        date_order=date_order,
    )

    flash(
        gettext(
            "Statement queued for background import and categorization. Track progress on the Jobs page. Job: {job_id}",
            job_id=job_id[:8],
        )
    )
    return redirect(url_for("upload.upload"))


@upload_bp.route("/upload/preview", methods=["POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def preview_statement_upload():
    """Return a read-only parsed CSV preview for a submitted statement upload.

    The request must include the same multipart fields as the final upload. The
    response is JSON and does not create statements, transactions, accounts, or
    background jobs.
    """
    uploaded_file = request.files.get("statement")
    statement_type_id = request.form.get("statement_type_id", "").strip()
    date_order = normalize_date_order(request.form.get("date_order"))

    with db_core_transaction() as conn:
        statement_type = get_statement_type_by_id(conn, statement_type_id)

        if statement_type is None:
            return preview_error(gettext("Please choose a valid statement type."))

        interac_direction = INTERAC_DIRECTION_AUTO
        if statement_type["parser_type"] == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
            interac_direction = normalize_interac_direction(request.form.get("interac_direction"))

        if uploaded_file is None or uploaded_file.filename == "":
            return preview_error(gettext("Please choose a statement file."))

        if not allowed_statement_file(uploaded_file.filename):
            allowed = ", ".join(sorted(settings.allowed_statement_extensions)).upper()
            return preview_error(gettext("Only {allowed} files are supported.", allowed=allowed))

        checksum = file_checksum(uploaded_file)
        existing = statement_by_checksum(conn, checksum)
        if existing:
            return preview_error(
                gettext(
                    (
                        "This statement was already uploaded as {filename} on {uploaded_at} "
                        "({status}). Use Retry import or Reprocess from Uploaded statements."
                    ),
                    filename=existing["filename"],
                    uploaded_at=existing["uploaded_at"],
                    status=existing["import_status"],
                )
            )

        extension = get_file_extension(uploaded_file.filename)
        if extension != "csv":
            return preview_error(gettext("Unsupported file type."))

        raw_text = decode_csv_statement_text(uploaded_file)
        preview = build_statement_preview(
            raw_text,
            statement_type["parser_type"],
            interac_direction=interac_direction,
            date_order=date_order,
        )

    return jsonify({"ok": True, "preview": preview})


def preview_error(message, status_code=400):
    """Return a JSON error response for the upload preview endpoint."""
    return jsonify({"ok": False, "message": message}), status_code


@upload_bp.route("/upload/<int:statement_id>/retry", methods=["POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def retry_statement_import(statement_id):
    """Queue another import attempt for an existing stored statement."""
    return queue_existing_statement_import(statement_id, reprocess=False)


@upload_bp.route("/upload/<int:statement_id>/reprocess", methods=["POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def reprocess_statement_import(statement_id):
    """Delete a statement's imported transactions and queue a fresh import."""
    return queue_existing_statement_import(statement_id, reprocess=True)


@upload_bp.route("/upload/<int:statement_id>/categorize-unknowns", methods=["POST"])
@permission_required(PERMISSION_IMPORT_STATEMENTS)
def categorize_statement_unknowns(statement_id):
    """Queue AI categorization for one statement's current unknown rows."""
    next_url = upload_redirect_target()

    with db_core_transaction() as conn:
        statement = statement_import_row(conn, statement_id)
        if statement is None:
            flash(gettext("Statement not found."))
            return redirect(next_url)

        if statement["import_status"] in ACTIVE_STATEMENT_IMPORT_STATUSES:
            flash(gettext("Wait for the statement import to finish before running AI categorization."))
            return redirect(next_url)

        unknown_count = upload_workflow.count_statement_unknown_transactions(conn, statement_id)
        if not unknown_count:
            flash(gettext("No unknown transactions need AI categorization for this statement."))
            return redirect(next_url)

    job_id = upload_workflow.queue_statement_llm_categorization(statement_id)
    flash(
        gettext(
            (
                "AI categorization queued for {count} unknown transaction. Track progress on the Jobs page. Job: {job_id}"
                if unknown_count == 1
                else "AI categorization queued for {count} unknown transactions. Track progress on the Jobs page. Job: {job_id}"
            ),
            count=unknown_count,
            job_id=job_id[:8],
        )
    )
    return redirect(next_url)


def queue_existing_statement_import(statement_id, reprocess=False):
    """Queue import work from stored statement text without accepting a new file."""
    next_url = upload_redirect_target()

    with db_core_transaction() as conn:
        statement = statement_import_row(conn, statement_id)
        if statement is None:
            flash(gettext("Statement not found."))
            return redirect(next_url)

        if statement["import_status"] in ACTIVE_STATEMENT_IMPORT_STATUSES:
            flash(gettext("This statement import is already queued or running."))
            return redirect(next_url)

        raw_text = statement["raw_text"] or ""
        if not raw_text.strip():
            flash(gettext("This statement has no stored text to import."))
            return redirect(next_url)

        if reprocess:
            delete_statement_transactions(conn, statement_id)

        extension = statement_extension(statement)
        upload_workflow.reset_statement_import_state(conn, statement_id)

    job_id = submit_statement_import_job(
        statement_id,
        statement["account_id"],
        statement["parser_type"],
        statement["import_mode"],
        extension,
        raw_text,
        statement["filename"],
        label_prefix="Reprocess" if reprocess else "Retry import",
        interac_direction=statement["interac_direction"],
        date_order=statement["date_order"],
    )
    flash(
        gettext(
            "{action} queued. Track progress on the Jobs page. Job: {job_id}",
            action=gettext("Reprocess" if reprocess else "Retry"),
            job_id=job_id[:8],
        )
    )
    return redirect(next_url)


def submit_statement_import_job(
    statement_id,
    account_id,
    parser_type,
    import_mode,
    extension,
    raw_text,
    filename,
    label_prefix="Import",
    interac_direction=INTERAC_DIRECTION_AUTO,
    date_order=DATE_ORDER_AUTO,
):
    """Submit statement import work with upload undo metadata."""
    interac_direction = normalize_interac_direction(interac_direction)
    date_order = normalize_date_order(date_order)
    undo_state = {}
    return submit_background_job(
        f"{label_prefix} {filename}",
        import_statement_transactions_job,
        statement_id,
        account_id,
        parser_type,
        extension,
        raw_text,
        import_mode=import_mode,
        interac_direction=interac_direction,
        date_order=date_order,
        undo_state=undo_state,
        undo_handler=undo_statement_upload_job,
        undo_args=(statement_id, undo_state),
    )


def upload_redirect_target():
    """Return a safe redirect target for upload actions."""
    target = request.form.get("next", "").strip()
    if target.startswith("/upload"):
        return target

    return url_for("upload.upload")


def read_statement_text(uploaded_file, extension):
    """Return decoded statement text for supported CSV uploads."""
    if extension == "csv":
        return decode_csv_statement_text(uploaded_file)

    flash(gettext("Unsupported file type."))
    return None


def decode_csv_statement_text(uploaded_file):
    """Decode an uploaded CSV stream and restore the stream position."""
    uploaded_file.stream.seek(0)
    raw_bytes = uploaded_file.stream.read()
    uploaded_file.stream.seek(0)
    return raw_bytes.decode("utf-8-sig", errors="replace")


def import_transactions(
    conn,
    statement_id,
    account_id,
    statement_type,
    extension,
    raw_text,
    undo_state=None,
    import_mode=None,
    interac_direction="auto",
    date_order=DATE_ORDER_AUTO,
):
    """Import transactions through the upload workflow with explicit helpers."""
    return upload_workflow.import_transactions(
        conn,
        statement_id,
        account_id,
        statement_type,
        extension,
        raw_text,
        undo_state=undo_state,
        import_mode=import_mode,
        interac_direction=interac_direction,
        date_order=date_order,
        categorizer=categorize_transactions,
        tag_setter=set_transaction_tags,
    )


import_statement_transactions_job = upload_workflow.import_statement_transactions_job
count_statement_unknown_transactions = upload_workflow.count_statement_unknown_transactions
categorize_statement_unknown_transactions_job = upload_workflow.categorize_statement_unknown_transactions_job
queue_statement_llm_categorization = upload_workflow.queue_statement_llm_categorization
undo_statement_upload_job = upload_workflow.undo_statement_upload_job
upload_result_message = upload_workflow.upload_result_message
