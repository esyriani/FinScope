"""Persistence helpers for user-pinned report views.

The repository owns writes to the ``pinned_reports`` table and returns detached
mapping rows for the Reports service layer. Callers must pass an active
SQLAlchemy Core connection, usually from ``db_core_transaction()``.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, func, insert, select, update

from finance_app.database.tables import pinned_reports as pinned_reports_table


def list_pinned_reports(conn: Any, user_id: int) -> list[Mapping[str, Any]]:
    """Return a user's pinned reports in deterministic display order."""
    return (
        conn.execute(
            select(pinned_reports_table)
            .where(pinned_reports_table.c.user_id == user_id)
            .order_by(pinned_reports_table.c.sort_order, pinned_reports_table.c.id)
        )
        .mappings()
        .fetchall()
    )


def get_pinned_report(conn: Any, user_id: int, pin_id: int) -> Mapping[str, Any] | None:
    """Return one pinned report owned by the user."""
    return (
        conn.execute(
            select(pinned_reports_table).where(
                pinned_reports_table.c.user_id == user_id,
                pinned_reports_table.c.id == pin_id,
            )
        )
        .mappings()
        .fetchone()
    )


def find_pinned_report_by_fingerprint(conn: Any, user_id: int, fingerprint: str) -> Mapping[str, Any] | None:
    """Return an existing exact pinned view for the user."""
    return (
        conn.execute(
            select(pinned_reports_table).where(
                pinned_reports_table.c.user_id == user_id,
                pinned_reports_table.c.fingerprint == fingerprint,
            )
        )
        .mappings()
        .fetchone()
    )


def count_pinned_reports(conn: Any, user_id: int) -> int:
    """Return how many report views the user has pinned."""
    return int(
        conn.execute(
            select(func.count()).select_from(pinned_reports_table).where(pinned_reports_table.c.user_id == user_id)
        ).scalar_one()
        or 0
    )


def next_sort_order(conn: Any, user_id: int) -> int:
    """Return the next sort order for a newly pinned report."""
    current_max = conn.execute(
        select(func.max(pinned_reports_table.c.sort_order)).where(pinned_reports_table.c.user_id == user_id)
    ).scalar_one()
    return int(current_max or 0) + (1 if current_max is not None else 0)


def insert_pinned_report(conn: Any, user_id: int, values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Insert a pinned report and return the persisted row."""
    insert_values = {**dict(values), "user_id": user_id}
    if "sort_order" not in insert_values:
        insert_values["sort_order"] = next_sort_order(conn, user_id)
    result = conn.execute(insert(pinned_reports_table).values(**insert_values))
    pin_id = int(result.inserted_primary_key[0])
    row = get_pinned_report(conn, user_id, pin_id)
    if row is None:
        raise RuntimeError("Pinned report could not be loaded after insert.")
    return row


def delete_pinned_reports(conn: Any, user_id: int, pin_ids: Sequence[int]) -> None:
    """Delete pinned reports owned by a user."""
    cleaned_ids = [int(pin_id) for pin_id in pin_ids]
    if not cleaned_ids:
        return
    conn.execute(
        delete(pinned_reports_table).where(
            pinned_reports_table.c.user_id == user_id,
            pinned_reports_table.c.id.in_(cleaned_ids),
        )
    )


def update_pinned_report_order_and_title(
    conn: Any,
    user_id: int,
    pin_id: int,
    sort_order: int,
    short_title: str | None,
) -> None:
    """Update one pinned report's display order and optional short title."""
    conn.execute(
        update(pinned_reports_table)
        .where(
            pinned_reports_table.c.user_id == user_id,
            pinned_reports_table.c.id == pin_id,
        )
        .values(sort_order=sort_order, short_title=short_title)
    )
