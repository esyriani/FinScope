"""Upload persistence helpers.

Provides SQLAlchemy Core queries and mutations for uploaded statement rows.
Callers own transaction boundaries through the central database helpers.
"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from finance_app.core.constants import (
    STATEMENT_IMPORT_STATUS_QUEUED,
    STATEMENT_IMPORT_STATUS_RUNNING,
    STATEMENT_IMPORT_STATUSES,
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
from finance_app.modules.statements.importer import get_file_extension, normalize_date_order

INACTIVE_STATEMENT_IMPORT_STATUSES = tuple(
    status
    for status in STATEMENT_IMPORT_STATUSES
    if status not in {STATEMENT_IMPORT_STATUS_QUEUED, STATEMENT_IMPORT_STATUS_RUNNING}
)


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


def new_statement_import_token() -> str:
    """Return a unique token for one statement import attempt."""
    return uuid4().hex


def reset_statement_import_state(
    conn: Any,
    statement_id: int,
    import_token: str,
    status: str = STATEMENT_IMPORT_STATUS_QUEUED,
) -> bool:
    """Reset persisted import metadata before queueing a statement import."""
    return update_statement_import_state(
        conn,
        statement_id,
        status,
        expected_statuses=INACTIVE_STATEMENT_IMPORT_STATUSES,
        import_token=import_token,
        import_error=None,
        import_started_at=None,
        import_finished_at=None,
        imported_count=0,
        skipped_count=0,
        ignored_count=0,
        llm_candidate_count=0,
    )


def claim_statement_import(conn: Any, statement_id: int, import_token: str, started_at: str) -> bool:
    """Claim one queued statement import attempt for execution."""
    return update_statement_import_state(
        conn,
        statement_id,
        STATEMENT_IMPORT_STATUS_RUNNING,
        expected_statuses=(STATEMENT_IMPORT_STATUS_QUEUED,),
        expected_import_token=import_token,
        import_error=None,
        import_started_at=started_at,
        import_finished_at=None,
    )


def update_statement_import_state(
    conn: Any,
    statement_id: int,
    status: str,
    *,
    expected_statuses: Sequence[str] | None = None,
    expected_import_token: str | None = None,
    import_token: str | None = None,
    **fields: Any,
) -> bool:
    """Persist import status, timestamps, counters, and errors for a statement."""
    allowed_fields = {
        "import_error",
        "import_started_at",
        "import_finished_at",
        "imported_count",
        "skipped_count",
        "ignored_count",
        "llm_candidate_count",
    }
    for field in fields:
        if field not in allowed_fields:
            raise ValueError(f"Unsupported statement import field: {field}")

    values = {"import_status": status, **fields}
    if import_token is not None:
        values["import_token"] = import_token

    statement = update(statements_table).where(statements_table.c.id == statement_id)
    if expected_statuses is not None:
        statement = statement.where(statements_table.c.import_status.in_(tuple(expected_statuses)))
    if expected_import_token is not None:
        statement = statement.where(statements_table.c.import_token == expected_import_token)

    cursor = conn.execute(statement.values(**values))
    return cursor.rowcount > 0


def statement_extension(statement: Mapping[str, Any]) -> str:
    """Return a stored or filename-derived extension for a statement row."""
    extension = (statement["extension"] or "").strip().lower()
    if extension:
        return extension

    filename = statement["filename"] or ""
    if "." not in filename:
        return ""

    return get_file_extension(filename)
