"""Tests for portable database insert fallback helpers."""

from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.exc import IntegrityError

from finance_app.core.config import sqlite_database_url
from finance_app.database.upsert import insert_or_select_unique_row


def sample_table():
    """Return a simple unique-key table for upsert helper tests."""
    metadata = MetaData()
    return Table(
        "sample",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("value", String(64), nullable=False, unique=True),
    )


def test_insert_or_select_unique_row_reselects_duplicate_and_keeps_transaction_usable(tmp_path):
    """Verify expected duplicate inserts leave the caller transaction usable."""
    table = sample_table()
    engine = create_engine(sqlite_database_url(tmp_path / "upsert.db"))
    try:
        table.metadata.create_all(engine)
        with engine.connect() as conn:
            with conn.begin():
                first_row, first_inserted = insert_or_select_unique_row(
                    conn,
                    insert(table).values(value="duplicate"),
                    select(table.c.id, table.c.value).where(table.c.value == "duplicate"),
                )
                second_row, second_inserted = insert_or_select_unique_row(
                    conn,
                    insert(table).values(value="duplicate"),
                    select(table.c.id, table.c.value).where(table.c.value == "duplicate"),
                )
                conn.execute(insert(table).values(value="after-conflict"))

            values = [
                row._mapping["value"]
                for row in conn.execute(select(table.c.value).order_by(table.c.value)).fetchall()
            ]

        assert first_inserted is True
        assert second_inserted is False
        assert second_row["id"] == first_row["id"]
        assert values == ["after-conflict", "duplicate"]
    finally:
        engine.dispose()


def test_insert_or_select_unique_row_reraises_unexpected_integrity_errors(tmp_path):
    """Verify non-reselectable integrity failures are not hidden."""
    table = sample_table()
    engine = create_engine(sqlite_database_url(tmp_path / "upsert.db"))
    try:
        table.metadata.create_all(engine)
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(insert(table).values(value="duplicate"))
                with pytest.raises(IntegrityError):
                    insert_or_select_unique_row(
                        conn,
                        insert(table).values(value="duplicate"),
                        select(table.c.id, table.c.value).where(table.c.value == "missing"),
                    )
                conn.execute(insert(table).values(value="after-error"))

            values = [
                row._mapping["value"]
                for row in conn.execute(select(table.c.value).order_by(table.c.value)).fetchall()
            ]

        assert values == ["after-error", "duplicate"]
    finally:
        engine.dispose()


class FakeMappingsResult:
    """Small result double for non-SQLite conflict path tests."""

    def __init__(self, row):
        """Store the mapping row returned by fetchone."""
        self.row = row

    def mappings(self):
        """Return a mapping-result compatible object."""
        return self

    def fetchone(self):
        """Return the configured row."""
        return self.row


class FakeNestedTransaction:
    """Context manager double for a SQLAlchemy savepoint."""

    def __init__(self, conn):
        """Store the connection double that owns this savepoint."""
        self.conn = conn

    def __enter__(self):
        """Mark the savepoint as opened."""
        self.conn.savepoint_opened = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        """Let SQLAlchemy-style exceptions propagate."""
        self.conn.savepoint_closed = True
        return False


class FakePostgresConnection:
    """Connection double that mimics PostgreSQL conflict behavior."""

    dialect = SimpleNamespace(name="postgresql")

    def __init__(self):
        """Initialize execution tracking."""
        self.savepoint_opened = False
        self.savepoint_closed = False
        self.executed = []

    def begin_nested(self):
        """Return a savepoint context manager."""
        return FakeNestedTransaction(self)

    def execute(self, statement):
        """Raise on the insert sentinel and return a row for reselects."""
        self.executed.append(statement)
        if statement == "insert":
            raise IntegrityError("insert", {}, RuntimeError("duplicate"))
        return FakeMappingsResult({"id": 7, "value": "duplicate"})


def test_insert_or_select_unique_row_uses_savepoint_for_non_sqlite_conflicts():
    """Verify non-SQLite expected conflicts are isolated with a savepoint."""
    conn = FakePostgresConnection()

    row, inserted = insert_or_select_unique_row(conn, "insert", "select")

    assert inserted is False
    assert row == {"id": 7, "value": "duplicate"}
    assert conn.savepoint_opened is True
    assert conn.savepoint_closed is True
    assert conn.executed == ["insert", "select"]
