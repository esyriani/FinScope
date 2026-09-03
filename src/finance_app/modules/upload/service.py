"""Application orchestration for the upload feature."""

from collections.abc import Mapping, MutableMapping
from typing import Any

from sqlalchemy.exc import IntegrityError as SqlAlchemyIntegrityError
from werkzeug.datastructures import FileStorage

from finance_app.background.runner import BackgroundJobSubmissionError, submit_background_job
from finance_app.core.config import settings
from finance_app.core.constants import (
    ACCOUNT_TYPE_CREDIT_CARD,
    ACCOUNT_TYPES,
    ACTIVE_STATEMENT_IMPORT_STATUSES,
    DATE_ORDER_AUTO,
    DATE_ORDERS,
    INTERAC_DIRECTION_AUTO,
    INTERAC_DIRECTIONS,
    STATEMENT_IMPORT_MODES,
    STATEMENT_IMPORT_STATUS_FAILED,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
    STATEMENT_TYPE_PARSER_TYPES,
    UNKNOWN_CATEGORY,
)
from finance_app.core.query import parse_page
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import normalize_name_key
from finance_app.modules.accounts.repository import find_account_by_name, get_or_create_account, normalize_account_type
from finance_app.modules.categories.categorization import categorize_transactions
from finance_app.modules.categories.llm_estimation import estimate_llm_categorization_tokens
from finance_app.modules.categories.repository import get_category_rules
from finance_app.modules.categories.sources import utc_timestamp
from finance_app.modules.settings.runtime import (
    confirm_ai_token_usage_enabled,
    get_int_setting,
    get_unknown_category,
)
from finance_app.modules.statements.importer import (
    allowed_statement_file,
    file_checksum,
    get_file_extension,
    normalize_date_order,
    normalize_interac_direction,
)
from finance_app.modules.statements.types import get_statement_type_by_id, get_statement_type_options
from finance_app.modules.upload import ai_workflow as upload_ai_workflow
from finance_app.modules.upload import workflow as upload_workflow
from finance_app.modules.upload.presenter import present_statement
from finance_app.modules.upload.preview import (
    build_statement_preview,
    decode_csv_statement_text,
    read_statement_text,
)
from finance_app.modules.upload.queries import (
    count_uploaded_statements,
    fetch_upload_accounts,
    fetch_uploaded_statements,
)
from finance_app.modules.upload.repository import (
    create_uploaded_statement,
    new_statement_import_token,
    reset_statement_import_state,
    statement_by_checksum,
    statement_extension,
    statement_import_row,
    update_statement_import_state,
)

STATEMENT_DUPLICATE_MESSAGE = (
    "This statement was already uploaded as {filename} on {uploaded_at} "
    "({status}). Use Retry import or Reprocess from Uploaded statements."
)
STATEMENT_IMPORT_QUEUE_FAILURE_MESSAGE = "Statement import could not be queued. Retry import from Uploaded statements."
AI_CATEGORIZATION_QUEUE_FAILURE_MESSAGE = "AI categorization could not be queued. Try again."


def build_upload_context(args: Any) -> dict[str, Any]:
    """Build upload context."""
    page = parse_page(args.get("page"))
    with db_core_transaction() as conn:
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        confirm_ai_token_usage = confirm_ai_token_usage_enabled(conn)
        statement_types = get_statement_type_options(conn)
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        accounts = fetch_upload_accounts(conn)
        total_count = count_uploaded_statements(conn)
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        statements = fetch_uploaded_statements(conn, unknown_category, page_size, offset)

    return {
        "statements": [present_statement(statement) for statement in statements],
        "statement_types": statement_types,
        "accounts": accounts,
        "account_types": ACCOUNT_TYPES,
        "interac_directions": INTERAC_DIRECTIONS,
        "date_orders": DATE_ORDERS,
        "statement_import_modes": STATEMENT_IMPORT_MODES,
        "statement_parser_types": STATEMENT_TYPE_PARSER_TYPES,
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "page_start": offset + 1 if total_count else 0,
        "page_end": min(offset + page_size, total_count),
        "confirm_ai_token_usage_enabled": confirm_ai_token_usage,
    }


def queue_uploaded_statement_import(uploaded_file: FileStorage | None, form: Any) -> dict[str, Any]:
    """Persist an uploaded statement and queue its background import workflow."""
    account_name = str(form.get("account_name", "Personal")).strip() or "Personal"
    paid_from_account_name = str(form.get("paid_from_account_name", "")).strip()
    statement_type_id = str(form.get("statement_type_id", "")).strip()
    date_order = normalize_date_order(form.get("date_order"))

    with db_core_transaction() as conn:
        statement_type = get_statement_type_by_id(conn, statement_type_id)
        if statement_type is None:
            return statement_action_error("Please choose a valid statement type.")

        interac_direction = INTERAC_DIRECTION_AUTO
        if statement_type["parser_type"] == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
            interac_direction = normalize_interac_direction(form.get("interac_direction"))

        if uploaded_file is None or uploaded_file.filename == "":
            return statement_action_error("Please choose a statement file.")

        filename = uploaded_file.filename or ""
        if not allowed_statement_file(filename):
            allowed = ", ".join(sorted(settings.allowed_statement_extensions)).upper()
            return statement_action_error("Only {allowed} files are supported.", allowed=allowed)

        checksum = file_checksum(uploaded_file)
        existing = statement_by_checksum(conn, checksum)
        if existing:
            return duplicate_statement_error(existing)

        extension = get_file_extension(filename)
        raw_text = read_statement_text(uploaded_file, extension)
        if raw_text is None:
            return statement_action_error("Unsupported file type.")

        preview = build_statement_preview(
            raw_text,
            statement_type["parser_type"],
            interac_direction=interac_direction,
            date_order=date_order,
        )
        if preview["date_format"]["requires_choice"]:
            return statement_action_error("Choose a statement date format before uploading.")

        # The statement import type controls parsing/import mode. The account
        # reporting role is stored separately so reports can classify card payments
        # and transfers without changing how the statement file is parsed.
        account_type = normalize_account_type(form.get("account_type") or statement_type["default_account_type"])
        if account_upload_metadata_conflicts(conn, account_name, account_type, paid_from_account_name):
            return statement_action_error(
                (
                    'Account "{account}" already exists with different reporting settings. '
                    "Use the existing settings or choose a different account name."
                ),
                account=account_name,
            )

        paid_from_value = (paid_from_account_name or None) if account_type == ACCOUNT_TYPE_CREDIT_CARD else ""
        try:
            with conn.begin_nested():
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
                    filename,
                    checksum,
                    extension,
                    interac_direction,
                    date_order,
                    raw_text,
                )
        except SqlAlchemyIntegrityError:
            existing = statement_by_checksum(conn, checksum)
            if existing is None:
                raise
            return duplicate_statement_error(existing)

        import_token = new_statement_import_token()
        if not reset_statement_import_state(conn, statement_id, import_token):
            return statement_action_error("This statement import is already queued or running.")

    try:
        job_id = submit_statement_import_job(
            statement_id,
            account["id"],
            statement_type["parser_type"],
            statement_type["import_mode"],
            extension,
            raw_text,
            filename,
            import_token,
            interac_direction=interac_direction,
            date_order=date_order,
        )
    except BackgroundJobSubmissionError as exc:
        mark_statement_import_queue_failed(statement_id, import_token, exc)
        return statement_action_error(STATEMENT_IMPORT_QUEUE_FAILURE_MESSAGE)

    return {"ok": True, "job_id": job_id}


def build_statement_upload_preview_response(uploaded_file: FileStorage | None, form: Any) -> dict[str, Any]:
    """Return a read-only parsed statement preview result for an upload form."""
    statement_type_id = str(form.get("statement_type_id", "")).strip()
    date_order = normalize_date_order(form.get("date_order"))

    with db_core_transaction() as conn:
        statement_type = get_statement_type_by_id(conn, statement_type_id)
        if statement_type is None:
            return statement_action_error("Please choose a valid statement type.", status_code=400)

        interac_direction = INTERAC_DIRECTION_AUTO
        if statement_type["parser_type"] == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
            interac_direction = normalize_interac_direction(form.get("interac_direction"))

        if uploaded_file is None or uploaded_file.filename == "":
            return statement_action_error("Please choose a statement file.", status_code=400)

        filename = uploaded_file.filename or ""
        if not allowed_statement_file(filename):
            allowed = ", ".join(sorted(settings.allowed_statement_extensions)).upper()
            return statement_action_error("Only {allowed} files are supported.", status_code=400, allowed=allowed)

        checksum = file_checksum(uploaded_file)
        existing = statement_by_checksum(conn, checksum)
        if existing:
            return duplicate_statement_error(existing, status_code=400)

        extension = get_file_extension(filename)
        if extension != "csv":
            return statement_action_error("Unsupported file type.", status_code=400)

        raw_text = decode_csv_statement_text(uploaded_file)
        preview = build_statement_preview(
            raw_text,
            statement_type["parser_type"],
            interac_direction=interac_direction,
            date_order=date_order,
        )

    return {"ok": True, "preview": preview}


def queue_existing_statement_import(statement_id: int, reprocess: bool = False) -> dict[str, Any]:
    """Queue import work from stored statement text without accepting a new file."""
    with db_core_transaction() as conn:
        statement = statement_import_row(conn, statement_id)
        if statement is None:
            return statement_action_error("Statement not found.")

        if statement["import_status"] in ACTIVE_STATEMENT_IMPORT_STATUSES:
            return statement_action_error("This statement import is already queued or running.")

        raw_text = statement["raw_text"] or ""
        if not raw_text.strip():
            return statement_action_error("This statement has no stored text to import.")

        extension = statement_extension(statement)
        import_token = new_statement_import_token()
        queued = reset_statement_import_state(conn, statement_id, import_token)
        if not queued:
            return statement_action_error("This statement import is already queued or running.")

    try:
        job_id = submit_statement_import_job(
            statement_id,
            statement["account_id"],
            statement["parser_type"],
            statement["import_mode"],
            extension,
            raw_text,
            statement["filename"],
            import_token,
            label_prefix="Reprocess" if reprocess else "Retry import",
            interac_direction=statement["interac_direction"],
            date_order=statement["date_order"],
            replace_existing_transactions=reprocess,
        )
    except BackgroundJobSubmissionError as exc:
        mark_statement_import_queue_failed(statement_id, import_token, exc)
        return statement_action_error(STATEMENT_IMPORT_QUEUE_FAILURE_MESSAGE)

    return {"ok": True, "job_id": job_id, "action": "Reprocess" if reprocess else "Retry"}


def statement_unknown_categorization_request(statement_id: int) -> dict[str, Any]:
    """Validate whether one statement currently has unknown rows for AI."""
    with db_core_transaction() as conn:
        statement = statement_import_row(conn, statement_id)
        if statement is None:
            return statement_action_error("Statement not found.", status_code=404)

        if statement["import_status"] in ACTIVE_STATEMENT_IMPORT_STATUSES:
            return statement_action_error(
                "Wait for the statement import to finish before running AI categorization.",
                status_code=400,
            )

        unknown_count = upload_workflow.count_statement_unknown_transactions(conn, statement_id)
        if not unknown_count:
            return statement_action_error(
                "No unknown transactions need AI categorization for this statement.",
                status_code=400,
            )

    return {"ok": True, "unknown_count": unknown_count}


def queue_statement_unknown_categorization(statement_id: int) -> dict[str, Any]:
    """Queue AI categorization for one statement after validating its state."""
    validation = statement_unknown_categorization_request(statement_id)
    if not validation["ok"]:
        return {**validation, "job_id": None}

    try:
        job_id = upload_workflow.queue_statement_llm_categorization(statement_id)
    except BackgroundJobSubmissionError:
        return statement_action_error(
            AI_CATEGORIZATION_QUEUE_FAILURE_MESSAGE,
            status_code=503,
            unknown_count=validation["unknown_count"],
        )
    return {**validation, "job_id": job_id}


def estimate_statement_unknown_categorization(statement_id: int) -> dict[str, Any]:
    """Validate and estimate AI token usage for one statement's unknown rows."""
    validation = statement_unknown_categorization_request(statement_id)
    if not validation["ok"]:
        return validation

    return estimate_statement_llm_categorization(statement_id)


def account_upload_metadata_conflicts(
    conn: Any,
    account_name: str,
    account_type: str,
    paid_from_account_name: str,
) -> bool:
    """Return whether upload metadata conflicts with an existing account row."""
    existing_account = find_account_by_name(conn, account_name)
    if existing_account is None:
        return False

    if existing_account["account_type"] != account_type:
        return True

    if account_type != ACCOUNT_TYPE_CREDIT_CARD or not paid_from_account_name:
        return False

    if normalize_name_key(paid_from_account_name) == normalize_name_key(account_name):
        return existing_account["paid_from_account_id"] is not None

    paid_from_account = find_account_by_name(conn, paid_from_account_name)
    if paid_from_account is None:
        return True

    return existing_account["paid_from_account_id"] != paid_from_account["id"]


def submit_statement_import_job(
    statement_id: int,
    account_id: int,
    parser_type: str,
    import_mode: str,
    extension: str,
    raw_text: str,
    filename: str,
    import_token: str,
    label_prefix: str = "Import",
    interac_direction: str = INTERAC_DIRECTION_AUTO,
    date_order: str = DATE_ORDER_AUTO,
    replace_existing_transactions: bool = False,
) -> str:
    """Submit statement import work with upload undo metadata."""
    interac_direction = normalize_interac_direction(interac_direction)
    date_order = normalize_date_order(date_order)
    undo_state: dict[str, Any] = {}
    undo_handler = None if replace_existing_transactions else upload_workflow.undo_statement_upload_job
    undo_args = None if replace_existing_transactions else (statement_id, undo_state)
    return submit_background_job(
        f"{label_prefix} {filename}",
        upload_workflow.import_statement_transactions_job,
        statement_id,
        account_id,
        parser_type,
        extension,
        raw_text,
        import_token,
        import_mode=import_mode,
        interac_direction=interac_direction,
        date_order=date_order,
        replace_existing_transactions=replace_existing_transactions,
        undo_state=undo_state,
        undo_handler=undo_handler,
        undo_args=undo_args,
    )


def mark_statement_import_queue_failed(
    statement_id: int,
    import_token: str,
    exc: BackgroundJobSubmissionError,
) -> None:
    """Mark the current statement import attempt failed after executor rejection."""
    with db_core_transaction() as conn:
        update_statement_import_state(
            conn,
            statement_id,
            STATEMENT_IMPORT_STATUS_FAILED,
            expected_statuses=ACTIVE_STATEMENT_IMPORT_STATUSES,
            expected_import_token=import_token,
            import_error=str(exc),
            import_finished_at=utc_timestamp(),
        )


def duplicate_statement_error(statement: Any, status_code: int | None = None) -> dict[str, Any]:
    """Return the standard duplicate statement upload error result."""
    return statement_action_error(
        STATEMENT_DUPLICATE_MESSAGE,
        status_code=status_code,
        filename=statement["filename"],
        uploaded_at=statement["uploaded_at"],
        status=statement["import_status"],
    )


def statement_action_error(message: str, status_code: int | None = None, **params: Any) -> dict[str, Any]:
    """Return a common upload action error result."""
    result: dict[str, Any] = {"ok": False, "message": message, "params": params}
    if status_code is not None:
        result["status_code"] = status_code
    return result


def estimate_statement_llm_categorization(statement_id: int) -> dict[str, Any]:
    """Return a token estimate for one statement's unknown AI categorization."""
    return estimate_unknown_llm_categorization(statement_id=statement_id, scope="statement_unknowns")


def estimate_all_unknown_llm_categorization() -> dict[str, Any]:
    """Return a token estimate for all active unknown AI categorization."""
    return estimate_unknown_llm_categorization(statement_id=None, scope="all_unknowns")


def estimate_unknown_llm_categorization(statement_id: int | None, scope: str) -> dict[str, Any]:
    """Return a JSON-ready token estimate for unknown transaction AI categorization."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn)
        rows = upload_ai_workflow.unknown_transaction_rows(conn, unknown_category, statement_id=statement_id)
        transactions: list[MutableMapping[str, Any]] = []
        for row in rows:
            transaction: MutableMapping[str, Any] = dict(row)
            transaction["category"] = row["category"] or unknown_category
            transactions.append(transaction)
        categorized = categorize_transactions(transactions, conn=conn) if transactions else []
        estimate = estimate_llm_categorization_tokens(
            conn,
            categorized,
            get_category_rules(conn),
            unknown_category,
        )

    return ai_token_estimate_result(scope, len(rows), estimate)


def ai_token_estimate_result(scope: str, transaction_count: int, estimate: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready token estimate payload for queued AI actions."""
    request_count = int(estimate.get("request_count") or 0)
    return {
        "ok": True,
        "scope": scope,
        "transaction_count": transaction_count,
        "message": (
            "No AI request would be sent for this action." if request_count == 0 else "AI usage estimate ready."
        ),
        "estimate": dict(estimate),
    }
