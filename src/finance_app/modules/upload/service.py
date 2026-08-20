"""Application orchestration for the upload feature."""

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError as SqlAlchemyIntegrityError
from werkzeug.datastructures import FileStorage

from finance_app.background.runner import submit_background_job
from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.config import settings
from finance_app.core.constants import (
    ACCOUNT_TYPE_CREDIT_CARD,
    ACCOUNT_TYPES,
    ACTIVE_STATEMENT_IMPORT_STATUSES,
    AMOUNT_COLUMNS,
    CREDIT_COLUMNS,
    DATE_COLUMNS,
    DATE_ORDER_AUTO,
    DATE_ORDER_DAY_FIRST,
    DATE_ORDER_MONTH_FIRST,
    DATE_ORDERS,
    DEBIT_COLUMNS,
    DESCRIPTION_COLUMNS,
    INTERAC_DIRECTION_AUTO,
    INTERAC_DIRECTION_RECEIVED,
    INTERAC_DIRECTION_SENT,
    INTERAC_DIRECTIONS,
    STATEMENT_IMPORT_MODES,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
    STATEMENT_TYPE_PARSER_TYPES,
    UNKNOWN_CATEGORY,
)
from finance_app.core.query import parse_page
from finance_app.core.text import normalize_header
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import normalize_name_key
from finance_app.database.tables import (
    statement_types as statement_types_table,
)
from finance_app.database.tables import (
    statements as statements_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.accounts.repository import find_account_by_name, get_or_create_account, normalize_account_type
from finance_app.modules.categories.categorization import categorize_transactions
from finance_app.modules.categories.llm_estimation import estimate_llm_categorization_tokens
from finance_app.modules.categories.repository import get_category_rules
from finance_app.modules.settings.runtime import (
    confirm_ai_token_usage_enabled,
    get_int_setting,
    get_statement_type_by_id,
    get_statement_type_options,
    get_unknown_category,
)
from finance_app.modules.statements.importer import (
    allowed_statement_file,
    analyze_slash_date_order,
    build_interac_transfer,
    build_transaction,
    csv_rows,
    date_formats_for_order,
    detect_csv_header,
    file_checksum,
    find_column,
    get_file_extension,
    normalize_date_order,
    normalize_interac_direction,
    parse_csv_transactions,
    parse_date,
    parse_money,
    preferred_date_formats_for_values,
)
from finance_app.modules.upload import ai_workflow as upload_ai_workflow
from finance_app.modules.upload import workflow as upload_workflow
from finance_app.modules.upload.repository import (
    create_uploaded_statement,
    delete_statement_transactions,
    new_statement_import_token,
    reset_statement_import_state,
    statement_by_checksum,
    statement_extension,
    statement_import_row,
)

STATEMENT_TEXT_PREVIEW_CHARS = 2000
STATEMENT_PREVIEW_ROW_LIMIT = 12
DATE_ORDER_OPTION_LABELS = {
    DATE_ORDER_MONTH_FIRST: DATE_ORDERS[DATE_ORDER_MONTH_FIRST],
    DATE_ORDER_DAY_FIRST: DATE_ORDERS[DATE_ORDER_DAY_FIRST],
}
STATEMENT_DUPLICATE_MESSAGE = (
    "This statement was already uploaded as {filename} on {uploaded_at} "
    "({status}). Use Retry import or Reprocess from Uploaded statements."
)


def build_upload_context(args: Any) -> dict[str, Any]:
    """Build upload context."""
    page = parse_page(args.get("page"))
    with db_core_transaction() as conn:
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        confirm_ai_token_usage = confirm_ai_token_usage_enabled(conn)
        statement_types = get_statement_type_options(conn)
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        accounts = (
            conn.execute(
                select(
                    accounts_table.c.id,
                    accounts_table.c.name,
                    accounts_table.c.account_type,
                    accounts_table.c.paid_from_account_id,
                ).order_by(func.lower(accounts_table.c.name), accounts_table.c.name)
            )
            .mappings()
            .fetchall()
        )
        total_count = conn.execute(select(func.count()).select_from(statements_table)).scalar_one()
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size

        paid_from_accounts = accounts_table.alias("paid_from_accounts")
        transaction_count = (
            select(func.count())
            .select_from(transactions_table)
            .where(transactions_table.c.statement_id == statements_table.c.id)
            .scalar_subquery()
        )
        unknown_transaction_count = (
            select(func.count())
            .select_from(transactions_table)
            .where(
                transactions_table.c.statement_id == statements_table.c.id,
                transactions_table.c.ignored == 0,
                transaction_category_label_expression(unknown_category) == unknown_category,
            )
            .scalar_subquery()
        )
        statements_join = (
            statements_table.outerjoin(
                accounts_table,
                accounts_table.c.id == statements_table.c.account_id,
            )
            .outerjoin(
                paid_from_accounts,
                paid_from_accounts.c.id == accounts_table.c.paid_from_account_id,
            )
            .outerjoin(
                statement_types_table,
                statement_types_table.c.id == statements_table.c.statement_type_id,
            )
        )
        statements = (
            conn.execute(
                select(
                    statements_table.c.id,
                    statements_table.c.filename,
                    statements_table.c.extension,
                    statements_table.c.interac_direction,
                    statements_table.c.date_order,
                    statement_types_table.c.name.label("statement_type_name"),
                    statement_types_table.c.parser_type,
                    statement_types_table.c.import_mode,
                    statements_table.c.uploaded_at,
                    statements_table.c.import_status,
                    statements_table.c.import_error,
                    statements_table.c.import_started_at,
                    statements_table.c.import_finished_at,
                    statements_table.c.imported_count,
                    statements_table.c.skipped_count,
                    statements_table.c.ignored_count,
                    statements_table.c.llm_candidate_count,
                    func.substr(
                        func.coalesce(statements_table.c.raw_text, ""),
                        1,
                        STATEMENT_TEXT_PREVIEW_CHARS,
                    ).label("raw_text_preview"),
                    func.length(func.coalesce(statements_table.c.raw_text, "")).label("raw_text_size"),
                    transaction_count.label("transaction_count"),
                    unknown_transaction_count.label("unknown_transaction_count"),
                    accounts_table.c.name.label("account_name"),
                    accounts_table.c.account_type.label("account_type"),
                    paid_from_accounts.c.name.label("paid_from_account_name"),
                )
                .select_from(statements_join)
                .order_by(statements_table.c.uploaded_at.desc())
                .limit(page_size)
                .offset(offset)
            )
            .mappings()
            .fetchall()
        )

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


def present_statement(statement: Any) -> dict[str, Any]:
    """Return a template-friendly representation of an uploaded statement row.

    The statement list only needs a bounded preview of the stored text. The full
    statement text can be large, so the query intentionally selects only a
    prefix and the total size.
    """
    row = dict(statement)
    raw_text_preview = row.get("raw_text_preview") or ""
    raw_text_size = row.get("raw_text_size") or 0
    row["raw_text_preview"] = raw_text_preview
    row["raw_text_truncated"] = raw_text_size > len(raw_text_preview)
    row["extension_label"] = (row.get("extension") or "").upper() or "n/a"
    row["date_order_label"] = DATE_ORDERS.get(row.get("date_order") or DATE_ORDER_AUTO, DATE_ORDERS[DATE_ORDER_AUTO])
    return row


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

        if reprocess:
            delete_statement_transactions(conn, statement_id)

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
    )
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

    job_id = upload_workflow.queue_statement_llm_categorization(statement_id)
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
) -> str:
    """Submit statement import work with upload undo metadata."""
    interac_direction = normalize_interac_direction(interac_direction)
    date_order = normalize_date_order(date_order)
    undo_state: dict[str, Any] = {}
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
        undo_state=undo_state,
        undo_handler=upload_workflow.undo_statement_upload_job,
        undo_args=(statement_id, undo_state),
    )


def read_statement_text(uploaded_file: FileStorage, extension: str) -> str | None:
    """Return decoded statement text for supported CSV uploads."""
    if extension == "csv":
        return decode_csv_statement_text(uploaded_file)

    return None


def decode_csv_statement_text(uploaded_file: FileStorage) -> str:
    """Decode an uploaded CSV stream and restore the stream position."""
    uploaded_file.stream.seek(0)
    raw_bytes = uploaded_file.stream.read()
    uploaded_file.stream.seek(0)
    return raw_bytes.decode("utf-8-sig", errors="replace")


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
        categorized = categorize_transactions(transactions, conn=conn, use_llm=False) if transactions else []
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


def build_statement_preview(
    raw_text: str,
    statement_type: str,
    interac_direction: str = INTERAC_DIRECTION_AUTO,
    date_order: str = DATE_ORDER_AUTO,
    preview_limit: int = STATEMENT_PREVIEW_ROW_LIMIT,
) -> dict[str, Any]:
    """Return a read-only import preview for an uploaded statement CSV.

    Args:
        raw_text: Decoded CSV contents from the submitted statement file.
        statement_type: Parser type selected for the upload.
        interac_direction: Optional Interac direction override.
        date_order: Optional user-selected numeric date order.
        preview_limit: Maximum parsed transaction rows to include.

    Returns:
        A JSON-serializable dictionary with row counts, date-order evidence,
        preview rows, and parsed date range details. The database is not read or
        mutated by this helper.
    """
    date_order = normalize_date_order(date_order)
    records = statement_preview_records(raw_text, statement_type, interac_direction)
    date_values = [record["raw_date"] for record in records]
    date_analysis = analyze_slash_date_order(date_values, date_order=date_order)
    date_formats = preferred_date_formats_for_values(date_values, date_order=date_order)
    parsed_rows = preview_rows_for_records(
        records,
        statement_type,
        interac_direction,
        date_formats,
        preview_limit,
        prefer_ambiguous_dates=date_analysis["ambiguous_count"] > 0,
    )
    parse_result = parse_csv_transactions(
        raw_text,
        statement_type=statement_type,
        interac_direction=interac_direction,
        date_order=date_order,
    )
    parsed_dates = [tx["tx_date"] for tx in parse_result["transactions"]]
    date_range = preview_date_range(parsed_dates)
    date_ranges: dict[str, dict[str, str]] = {}
    if date_analysis["has_date_order_dates"]:
        date_ranges = {
            DATE_ORDER_MONTH_FIRST: preview_date_range_for_order(
                raw_text,
                statement_type,
                interac_direction,
                DATE_ORDER_MONTH_FIRST,
            ),
            DATE_ORDER_DAY_FIRST: preview_date_range_for_order(
                raw_text,
                statement_type,
                interac_direction,
                DATE_ORDER_DAY_FIRST,
            ),
        }
        effective_order = date_analysis["effective_order"]
        if effective_order in date_ranges:
            date_range = date_ranges[effective_order]
        elif date_analysis["requires_choice"]:
            date_range = preview_date_range([])

    return {
        "transaction_count": len(parse_result["transactions"]),
        "ignored_rows": parse_result["ignored_rows"],
        "preview_rows": parsed_rows,
        "date_range": date_range,
        "date_ranges": date_ranges,
        "date_format": preview_date_format_payload(date_analysis),
    }


def preview_date_range(parsed_dates: Sequence[str]) -> dict[str, str]:
    """Return a JSON-ready date range for parsed transaction dates."""
    return {
        "earliest": min(parsed_dates) if parsed_dates else "",
        "latest": max(parsed_dates) if parsed_dates else "",
    }


def preview_date_range_for_order(
    raw_text: str,
    statement_type: str,
    interac_direction: str,
    date_order: str,
) -> dict[str, str]:
    """Return the parsed date range for one numeric date-order option."""
    parse_result = parse_csv_transactions(
        raw_text,
        statement_type=statement_type,
        interac_direction=interac_direction,
        date_order=date_order,
    )
    return preview_date_range([tx["tx_date"] for tx in parse_result["transactions"]])


def preview_date_format_payload(date_analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Return date-format metadata for the upload preview modal."""
    effective_order = date_analysis["effective_order"]
    if effective_order not in {DATE_ORDER_MONTH_FIRST, DATE_ORDER_DAY_FIRST}:
        effective_order = ""

    return {
        **date_analysis,
        "effective_order": effective_order,
        "options": [{"value": value, "label": label} for value, label in DATE_ORDER_OPTION_LABELS.items()],
    }


def statement_preview_records(
    raw_text: str,
    statement_type: str,
    interac_direction: str = INTERAC_DIRECTION_AUTO,
) -> list[dict[str, Any]]:
    """Return normalized source rows needed for statement preview parsing."""
    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        return interac_preview_records(raw_text, interac_direction=interac_direction)
    return ledger_preview_records(raw_text)


def ledger_preview_records(raw_text: str) -> list[dict[str, Any]]:
    """Return source-row fields for bank and card statement previews."""
    rows = csv_rows(raw_text)
    header_index, header = detect_csv_header(rows)
    if header is not None:
        header_index = header_index or 0
        header_map = {normalize_header(cell): cell for cell in header if normalize_header(cell)}
        date_col = find_column(header_map, DATE_COLUMNS)
        description_col = find_column(header_map, DESCRIPTION_COLUMNS)
        debit_col = find_column(header_map, DEBIT_COLUMNS)
        credit_col = find_column(header_map, CREDIT_COLUMNS)
        amount_col = find_column(header_map, AMOUNT_COLUMNS)
        records = []
        for row in rows[header_index + 1 :]:
            padded_row = row + [""] * max(0, len(header) - len(row))
            record = dict(zip(header, padded_row))
            records.append(
                {
                    "raw_date": record.get(date_col or ""),
                    "description": record.get(description_col or ""),
                    "raw_debit": record.get(debit_col) if debit_col else None,
                    "raw_credit": record.get(credit_col) if credit_col else None,
                    "raw_amount": record.get(amount_col) if amount_col else None,
                }
            )
        return records

    fallback_records: list[dict[str, Any]] = []
    for row in rows:
        if len(row) < 3:
            continue
        fallback_records.append(
            {
                "raw_date": row[0],
                "description": row[1],
                "raw_debit": row[2] if len(row) > 3 else None,
                "raw_credit": row[3] if len(row) > 3 else None,
                "raw_amount": row[2] if len(row) == 3 else None,
            }
        )
    return fallback_records


def interac_preview_records(
    raw_text: str,
    interac_direction: str = INTERAC_DIRECTION_AUTO,
) -> list[dict[str, Any]]:
    """Return source-row fields for Interac statement previews."""
    rows = csv_rows(raw_text)
    if not rows:
        return []

    header = rows[0]
    header_map = {normalize_header(cell): cell for cell in header if normalize_header(cell)}
    sent_date_col = find_column(header_map, {"datesent"})
    deposited_date_col = find_column(header_map, {"datedeposited"})
    recipient_col = find_column(header_map, {"recipient"})
    received_from_col = find_column(header_map, {"receivedfrom"})
    amount_col = find_column(header_map, {"amount"})
    method_col = find_column(header_map, {"method"})
    status_col = find_column(header_map, {"status"})
    direction = normalize_interac_direction(interac_direction)

    if direction in {INTERAC_DIRECTION_SENT, INTERAC_DIRECTION_RECEIVED}:
        date_col = sent_date_col or deposited_date_col or find_column(header_map, DATE_COLUMNS)
        merchant_col = (
            recipient_col or received_from_col or find_column(header_map, DESCRIPTION_COLUMNS | {"counterparty"})
        )
    elif sent_date_col and recipient_col:
        date_col = sent_date_col
        merchant_col = recipient_col
    elif deposited_date_col and received_from_col:
        date_col = deposited_date_col
        merchant_col = received_from_col
    else:
        date_col = find_column(header_map, DATE_COLUMNS)
        merchant_col = find_column(header_map, DESCRIPTION_COLUMNS | {"counterparty"})

    records: list[dict[str, Any]] = []
    for row in rows[1:]:
        padded_row = row + [""] * max(0, len(header) - len(row))
        record = dict(zip(header, padded_row))
        records.append(
            {
                "raw_date": record.get(date_col or ""),
                "description": record.get(merchant_col or ""),
                "raw_amount": record.get(amount_col) if amount_col else None,
                "method": record.get(method_col) if method_col else None,
                "status": record.get(status_col) if status_col else None,
            }
        )
    return records


def preview_rows_for_records(
    records: Sequence[Mapping[str, Any]],
    statement_type: str,
    interac_direction: str,
    date_formats: Sequence[str],
    preview_limit: int,
    prefer_ambiguous_dates: bool = False,
) -> list[dict[str, Any]]:
    """Return parsed preview rows for transaction-like records.

    Args:
        records: Normalized source rows extracted from the uploaded statement.
        statement_type: Parser type selected for the statement.
        interac_direction: Optional Interac direction override for transfer files.
        date_formats: Ordered parser date formats for the effective date order.
        preview_limit: Maximum number of rows to return.
        prefer_ambiguous_dates: When true, rows that parse differently under
            month-first and day-first numeric date orders are shown before other
            sample rows.

    Returns:
        A list of JSON-ready preview rows. The source statement is not mutated.
    """
    preview_rows: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    for record in records:
        tx = preview_transaction(record, statement_type, interac_direction, date_formats)
        if tx is None:
            continue

        row = preview_row_payload(record, tx)
        if prefer_ambiguous_dates:
            if preview_row_has_ambiguous_date(row):
                preview_rows.append(row)
                if len(preview_rows) >= preview_limit:
                    break
            elif len(fallback_rows) < preview_limit:
                fallback_rows.append(row)
            continue

        preview_rows.append(row)
        if len(preview_rows) >= preview_limit:
            break

    if prefer_ambiguous_dates and len(preview_rows) < preview_limit:
        preview_rows.extend(fallback_rows[: preview_limit - len(preview_rows)])

    return preview_rows


def preview_row_has_ambiguous_date(row: Mapping[str, Any]) -> bool:
    """Return whether a preview row has conflicting numeric date interpretations."""
    month_first_date = row.get("month_first_date") or ""
    day_first_date = row.get("day_first_date") or ""
    return bool(month_first_date and day_first_date and month_first_date != day_first_date)


def preview_transaction(
    record: Mapping[str, Any],
    statement_type: str,
    interac_direction: str,
    date_formats: Sequence[str],
) -> dict[str, Any] | None:
    """Parse one preview source row using the selected statement parser."""
    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        direction = normalize_interac_direction(interac_direction)
        if direction == INTERAC_DIRECTION_AUTO:
            direction = INTERAC_DIRECTION_SENT
        return build_interac_transfer(
            record.get("raw_date"),
            record.get("description"),
            record.get("raw_amount"),
            direction,
            method=record.get("method"),
            status=record.get("status"),
            require_deposited_status=bool(record.get("status")),
            date_formats=date_formats,
        )

    return build_transaction(
        record.get("raw_date"),
        record.get("description"),
        statement_type,
        raw_debit=record.get("raw_debit"),
        raw_credit=record.get("raw_credit"),
        raw_amount=record.get("raw_amount"),
        date_formats=date_formats,
    )


def preview_row_payload(record: Mapping[str, Any], tx: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready row for the statement preview modal."""
    raw_date = record.get("raw_date")
    return {
        "raw_date": str(raw_date or ""),
        "parsed_date": tx["tx_date"],
        "month_first_date": parse_date(
            raw_date,
            date_formats=date_formats_for_order(DATE_ORDER_MONTH_FIRST),
        )
        or "",
        "day_first_date": parse_date(
            raw_date,
            date_formats=date_formats_for_order(DATE_ORDER_DAY_FIRST),
        )
        or "",
        "description": tx["description"],
        "amount": f"{tx['amount']:.2f}",
        "raw_amount": preview_raw_amount(record),
    }


def preview_raw_amount(record: Mapping[str, Any]) -> str:
    """Return the most useful original amount text for one preview record."""
    if record.get("raw_debit"):
        return str(record.get("raw_debit"))
    if record.get("raw_credit"):
        return str(record.get("raw_credit"))
    if record.get("raw_amount"):
        return str(record.get("raw_amount"))

    amount = parse_money(record.get("raw_amount"))
    return "" if amount is None else f"{amount:.2f}"
