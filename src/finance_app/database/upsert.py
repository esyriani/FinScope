"""Portable insert fallback helpers.

Provides narrow SQLAlchemy Core helpers for select-then-insert repository
paths. Callers pass unique-key selects so concurrent unique conflicts can be
handled without relying on dialect-specific upsert syntax.
"""

from sqlalchemy.exc import IntegrityError as SqlAlchemyIntegrityError


def insert_or_select_unique_row(conn, insert_statement, select_statement):
    """Insert a row or reselect it after a concurrent unique conflict.

    Args:
        conn: Open SQLAlchemy Core connection managed by the caller.
        insert_statement: SQLAlchemy Core insert statement for the desired row.
        select_statement: SQLAlchemy Core select statement that finds the row by
            the same logical unique key.

    Returns:
        A tuple of ``(row, inserted)`` where ``row`` is the selected mapping row
        and ``inserted`` is true only when this call inserted the row.

    Raises:
        IntegrityError: Re-raised when the insert failed but the unique-key
            reselect did not find a row, which means the integrity failure was
            not the expected concurrent duplicate.
    """
    try:
        execute_conflict_safe_insert(conn, insert_statement)
    except SqlAlchemyIntegrityError as exc:
        row = conn.execute(select_statement).mappings().fetchone()
        if row is None:
            raise exc
        return row, False

    return conn.execute(select_statement).mappings().fetchone(), True


def execute_conflict_safe_insert(conn, insert_statement):
    """Execute an insert so expected duplicates do not poison transactions.

    PostgreSQL aborts a transaction after an integrity error until a rollback, so
    non-SQLite dialects use a savepoint around the expected-conflict insert.
    SQLite can continue after a statement-level integrity error, and avoiding a
    savepoint there keeps the insert bound to the caller's outer transaction.
    """
    if connection_dialect_name(conn) == "sqlite":
        return conn.execute(insert_statement)

    with conn.begin_nested():
        return conn.execute(insert_statement)


def connection_dialect_name(conn):
    """Return the SQLAlchemy dialect name for a Core connection-like object."""
    dialect = getattr(conn, "dialect", None)
    if dialect is None and hasattr(conn, "_conn"):
        dialect = getattr(conn._conn, "dialect", None)
    return getattr(dialect, "name", "")
