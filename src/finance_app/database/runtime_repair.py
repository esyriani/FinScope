"""Startup repair helpers for persisted state tied to process-local workers.

The background runner intentionally keeps job state in memory. These helpers
repair durable rows that would otherwise keep pointing at work that disappeared
when the process stopped.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update

from finance_app.core.constants import ACTIVE_STATEMENT_IMPORT_STATUSES, STATEMENT_IMPORT_STATUS_FAILED
from finance_app.database.tables import statements as statements_table

INTERRUPTED_STATEMENT_IMPORT_ERROR = (
    "The app restarted before this statement import finished. Retry import or reprocess the statement."
)


def repair_startup_runtime_state(conn: Any) -> dict[str, int]:
    """Repair persisted runtime state that cannot survive a process restart."""
    return {
        "interrupted_statement_imports": mark_interrupted_statement_imports_failed(conn),
    }


def mark_interrupted_statement_imports_failed(conn: Any, finished_at: str | None = None) -> int:
    """Mark queued or running statement imports failed so they can be retried."""
    result = conn.execute(
        update(statements_table)
        .where(statements_table.c.import_status.in_(ACTIVE_STATEMENT_IMPORT_STATUSES))
        .values(
            import_status=STATEMENT_IMPORT_STATUS_FAILED,
            import_error=INTERRUPTED_STATEMENT_IMPORT_ERROR,
            import_finished_at=finished_at or utc_timestamp(),
        )
    )
    return max(0, result.rowcount or 0)


def utc_timestamp() -> str:
    """Return a UTC timestamp for startup repair metadata."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
