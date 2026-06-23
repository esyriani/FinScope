"""Application orchestration for the upload feature."""

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from sqlalchemy import func, select

from finance_app.core.config import settings
from finance_app.core.constants import (
    ACCOUNT_TYPES,
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
from finance_app.database.tables import (
    statement_types as statement_types_table,
)
from finance_app.database.tables import (
    statements as statements_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.categorization import categorize_transactions
from finance_app.modules.categories.llm_estimation import estimate_llm_categorization_tokens
from finance_app.modules.categories.repository import get_category_rules
from finance_app.modules.settings.runtime import (
    confirm_ai_token_usage_enabled,
    get_int_setting,
    get_statement_type_options,
    get_unknown_category,
)
from finance_app.modules.statements.importer import (
    analyze_slash_date_order,
    build_interac_transfer,
    build_transaction,
    csv_rows,
    date_formats_for_order,
    detect_csv_header,
    find_column,
    normalize_date_order,
    normalize_interac_direction,
    parse_csv_transactions,
    parse_date,
    parse_money,
    preferred_date_formats_for_values,
)
from finance_app.modules.upload import workflow as upload_workflow

STATEMENT_TEXT_PREVIEW_CHARS = 2000
STATEMENT_PREVIEW_ROW_LIMIT = 12
DATE_ORDER_OPTION_LABELS = {
    DATE_ORDER_MONTH_FIRST: DATE_ORDERS[DATE_ORDER_MONTH_FIRST],
    DATE_ORDER_DAY_FIRST: DATE_ORDERS[DATE_ORDER_DAY_FIRST],
}


def build_upload_context(args: Any) -> dict[str, Any]:
    """Build upload context."""
    page = parse_page(args.get("page"))
    with db_core_transaction() as conn:
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        confirm_ai_token_usage = confirm_ai_token_usage_enabled(conn)
        statement_types = get_statement_type_options(conn)
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
                (transactions_table.c.category.is_(None) | (transactions_table.c.category == UNKNOWN_CATEGORY)),
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
        rows = upload_workflow.unknown_transaction_rows(conn, unknown_category, statement_id=statement_id)
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
