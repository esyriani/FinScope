"""Application orchestration for the upload feature."""

from sqlalchemy import func, select

from finance_app.core.config import settings
from finance_app.core.constants import (
    ACCOUNT_TYPES,
    INTERAC_DIRECTIONS,
    STATEMENT_IMPORT_MODES,
    STATEMENT_TYPE_PARSER_TYPES,
    UNKNOWN_CATEGORY,
)
from finance_app.core.query import parse_page
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
    statements as statements_table,
    statement_types as statement_types_table,
    transactions as transactions_table,
)
from finance_app.modules.settings.runtime import get_int_setting, get_statement_type_options

STATEMENT_TEXT_PREVIEW_CHARS = 2000


def build_upload_context(args):
    """Build upload context."""
    page = parse_page(args.get("page"))
    with db_core_transaction() as conn:
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        statement_types = get_statement_type_options(conn)
        accounts = conn.execute(
            select(
                accounts_table.c.id,
                accounts_table.c.name,
                accounts_table.c.account_type,
                accounts_table.c.paid_from_account_id,
            ).order_by(func.lower(accounts_table.c.name), accounts_table.c.name)
        ).mappings().fetchall()
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
                (
                    transactions_table.c.category.is_(None)
                    | (transactions_table.c.category == UNKNOWN_CATEGORY)
                ),
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
        statements = conn.execute(
            select(
                statements_table.c.id,
                statements_table.c.filename,
                statements_table.c.extension,
                statements_table.c.interac_direction,
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
        ).mappings().fetchall()

    return {
        "statements": [present_statement(statement) for statement in statements],
        "statement_types": statement_types,
        "accounts": accounts,
        "account_types": ACCOUNT_TYPES,
        "interac_directions": INTERAC_DIRECTIONS,
        "statement_import_modes": STATEMENT_IMPORT_MODES,
        "statement_parser_types": STATEMENT_TYPE_PARSER_TYPES,
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "page_start": offset + 1 if total_count else 0,
        "page_end": min(offset + page_size, total_count),
    }


def present_statement(statement):
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
    return row
