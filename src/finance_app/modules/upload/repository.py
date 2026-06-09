"""Upload persistence helpers.

Provides SQLAlchemy Core queries and mutations for uploaded statement rows.
Callers own transaction boundaries through the central database helpers.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, insert, select

from finance_app.database.tables import (
    statement_types as statement_types_table,
)
from finance_app.database.tables import (
    statements as statements_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.statements.importer import get_file_extension, normalize_date_order


def statement_import_row(conn: Any, statement_id: int) -> Any:
    """Return persisted statement data needed to queue import work."""
    return (
        conn.execute(
            select(
                statements_table.c.id,
                statements_table.c.account_id,
                statements_table.c.filename,
                statements_table.c.extension,
                statements_table.c.raw_text,
                statements_table.c.import_status,
                statements_table.c.interac_direction,
                statements_table.c.date_order,
                statement_types_table.c.parser_type,
                statement_types_table.c.import_mode,
            )
            .select_from(
                statements_table.join(
                    statement_types_table,
                    statement_types_table.c.id == statements_table.c.statement_type_id,
                )
            )
            .where(statements_table.c.id == statement_id)
        )
        .mappings()
        .fetchone()
    )


def statement_by_checksum(conn: Any, checksum: str) -> Any:
    """Return an uploaded statement row by checksum for duplicate detection."""
    return (
        conn.execute(
            select(
                statements_table.c.id,
                statements_table.c.filename,
                statements_table.c.uploaded_at,
                statements_table.c.import_status,
            ).where(statements_table.c.checksum == checksum)
        )
        .mappings()
        .fetchone()
    )


def create_uploaded_statement(
    conn: Any,
    account_id: int | None,
    statement_type_id: int,
    filename: str,
    checksum: str,
    extension: str,
    interac_direction: str,
    date_order: object,
    raw_text: str,
) -> int:
    """Insert an uploaded statement row and return its ID."""
    date_order = normalize_date_order(date_order)
    result = conn.execute(
        insert(statements_table).values(
            account_id=account_id,
            statement_type_id=statement_type_id,
            filename=filename,
            checksum=checksum,
            extension=extension,
            interac_direction=interac_direction,
            date_order=date_order,
            raw_text=raw_text,
        )
    )
    return result.inserted_primary_key[0]


def delete_statement_transactions(conn: Any, statement_id: int) -> None:
    """Delete imported transactions for a statement before reprocessing."""
    conn.execute(delete(transactions_table).where(transactions_table.c.statement_id == statement_id))


def statement_extension(statement: Mapping[str, Any]) -> str:
    """Return a stored or filename-derived extension for a statement row."""
    extension = (statement["extension"] or "").strip().lower()
    if extension:
        return extension

    filename = statement["filename"] or ""
    if "." not in filename:
        return ""

    return get_file_extension(filename)
