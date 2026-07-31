"""Tests for SQLAlchemy Core engine and connection lifecycle."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask
from sqlalchemy import create_engine, insert, inspect, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError

from finance_app.core.config import sqlite_database_url
from finance_app.database import connection as connection_module
from finance_app.database import engine as engine_module
from finance_app.database.engine import (
    create_database_engine,
    db_core_transaction,
    dispose_database_engine,
    ensure_database_exists,
    get_core_connection,
    get_database_engine,
    register_core_db,
)
from finance_app.database.tables import categories as categories_table


@pytest.fixture(autouse=True)
def reset_engine_cache():
    """Reset the cached SQLAlchemy engine around each test."""
    dispose_database_engine()
    yield
    dispose_database_engine()


def test_get_database_engine_reuses_configured_engine(tmp_path):
    """Reuse the cached engine until the configured database URL changes."""
    first_url = sqlite_database_url(tmp_path / "first.db")
    second_url = sqlite_database_url(tmp_path / "second.db")

    with patch.object(engine_module, "settings", SimpleNamespace(database_url=first_url)):
        first_engine = get_database_engine()
        assert get_database_engine() is first_engine

    with patch.object(engine_module, "settings", SimpleNamespace(database_url=second_url)):
        second_engine = get_database_engine()

    assert second_engine is not first_engine
    assert first_engine.pool is not second_engine.pool


def test_create_database_engine_ensures_mysql_database_before_app_engine():
    """Create the server-level database before building a MySQL app engine."""
    database_url = "mysql+pymysql://root@127.0.0.1:3306/finscope?charset=utf8mb4"

    with (
        patch.object(engine_module, "ensure_database_exists") as ensure_database,
        patch.object(engine_module, "create_engine") as create_engine_mock,
    ):
        create_database_engine(database_url)

    ensure_database.assert_called_once_with(database_url)
    create_engine_mock.assert_called_once_with(database_url, pool_pre_ping=True)


def test_ensure_database_exists_creates_mysql_schema_from_server_url():
    """Use a server-level URL when auto-creating the configured MySQL database."""
    executed = []

    class DummyConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, statement):
            executed.append(str(statement))

    server_engine = SimpleNamespace(
        dialect=mysql.dialect(),
        connect=lambda: DummyConnection(),
        dispose=lambda: None,
    )

    with patch.object(engine_module, "create_engine", return_value=server_engine) as create_engine_mock:
        ensure_database_exists("mysql+pymysql://root@127.0.0.1:3306/finscope?charset=utf8mb4")

    server_url = create_engine_mock.call_args.args[0]
    assert server_url.database is None
    assert server_url.query["charset"] == "utf8mb4"
    assert create_engine_mock.call_args.kwargs == {
        "isolation_level": "AUTOCOMMIT",
        "pool_pre_ping": True,
    }
    assert executed == ["CREATE DATABASE IF NOT EXISTS `finscope` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"]


def test_ensure_database_exists_ignores_sqlite_urls():
    """Keep SQLite behavior delegated to SQLAlchemy's normal file creation."""
    with patch.object(engine_module, "create_engine") as create_engine_mock:
        ensure_database_exists("sqlite:///finscope.db")

    create_engine_mock.assert_not_called()


def test_core_request_connection_reuses_until_teardown(tmp_path):
    """Reuse a request-scoped Core connection and close it during teardown."""
    database_url = sqlite_database_url(tmp_path / "finscope.db")
    app = Flask(__name__)
    register_core_db(app)
    seen_connections = []

    @app.route("/reuse")
    def reuse_connection():
        """Return whether one request receives one Core connection."""
        first = get_core_connection()
        second = get_core_connection()
        seen_connections.append(first)
        return "same" if first is second else "different"

    with patch.object(engine_module, "settings", SimpleNamespace(database_url=database_url)):
        response = app.test_client().get("/reuse")

    assert response.data == b"same"
    assert seen_connections[0].closed


def test_core_request_teardown_rolls_back_uncommitted_work(tmp_path):
    """Rollback uncommitted request work when Flask tears down the context."""
    database_path = tmp_path / "finscope.db"
    database_url = sqlite_database_url(database_path)
    setup_engine = create_engine(database_url)
    try:
        with setup_engine.begin() as conn:
            conn.execute(text("CREATE TABLE sample (value TEXT NOT NULL)"))
    finally:
        setup_engine.dispose()

    app = Flask(__name__)
    register_core_db(app)

    @app.route("/uncommitted")
    def uncommitted_write():
        """Write without committing so teardown must roll it back."""
        get_core_connection().execute(text("INSERT INTO sample (value) VALUES ('rolled back')"))
        return "ok"

    with patch.object(engine_module, "settings", SimpleNamespace(database_url=database_url)):
        response = app.test_client().get("/uncommitted")

    assert response.status_code == 200
    verification_engine = create_engine(database_url)
    try:
        with verification_engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM sample")).scalar_one()
    finally:
        verification_engine.dispose()
    assert count == 0


def test_core_transaction_commits_after_request_scoped_read(tmp_path):
    """Commit managed writes even when an earlier request read opened a transaction."""
    database_path = tmp_path / "finscope.db"
    database_url = sqlite_database_url(database_path)
    setup_engine = create_engine(database_url)
    try:
        with setup_engine.begin() as conn:
            conn.execute(text("CREATE TABLE sample (value TEXT NOT NULL)"))
    finally:
        setup_engine.dispose()

    app = Flask(__name__)
    register_core_db(app)

    @app.route("/read-then-write")
    def read_then_write():
        """Run a request-scoped read before a managed write transaction."""
        get_core_connection().execute(text("SELECT COUNT(*) FROM sample")).scalar_one()
        with db_core_transaction() as conn:
            conn.execute(text("INSERT INTO sample (value) VALUES ('committed')"))
        return "ok"

    with patch.object(engine_module, "settings", SimpleNamespace(database_url=database_url)):
        response = app.test_client().get("/read-then-write")

    assert response.status_code == 200
    verification_engine = create_engine(database_url)
    try:
        with verification_engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM sample")).scalar_one()
    finally:
        verification_engine.dispose()
    assert count == 1


def test_core_request_nested_transaction_without_conn_uses_savepoint(tmp_path):
    """Keep a request-owned outer transaction open across nested helper calls."""
    database_path = tmp_path / "finscope.db"
    database_url = sqlite_database_url(database_path)
    setup_engine = create_engine(database_url)
    try:
        with setup_engine.begin() as conn:
            conn.execute(text("CREATE TABLE sample (value TEXT NOT NULL)"))
    finally:
        setup_engine.dispose()

    app = Flask(__name__)
    register_core_db(app)
    active_after_inner = []

    @app.route("/nested-rollback")
    def nested_rollback():
        """Rollback outer and nested writes when the outer block fails."""
        try:
            with db_core_transaction() as outer:
                outer.execute(text("INSERT INTO sample (value) VALUES ('outer')"))
                with db_core_transaction() as inner:
                    inner.execute(text("INSERT INTO sample (value) VALUES ('nested')"))
                active_after_inner.append(outer.in_transaction())
                raise RuntimeError("force outer rollback")
        except RuntimeError:
            return "active" if active_after_inner[0] else "inactive"

    with patch.object(engine_module, "settings", SimpleNamespace(database_url=database_url)):
        response = app.test_client().get("/nested-rollback")

    assert response.data == b"active"
    verification_engine = create_engine(database_url)
    try:
        with verification_engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM sample")).scalar_one()
    finally:
        verification_engine.dispose()
    assert count == 0


def test_app_request_nested_transaction_without_conn_keeps_outer_boundary(app):
    """Verify real app request-scoped no-arg nesting cannot commit the outer block."""
    outer_category = "Request scoped outer rollback"
    nested_category = "Request scoped nested rollback"
    active_after_inner = []

    with app.test_request_context("/request-scoped-nested-transaction"):
        with pytest.raises(RuntimeError, match="force outer rollback"):
            with db_core_transaction() as outer:
                outer.execute(insert(categories_table).values(name=outer_category))

                with db_core_transaction() as inner:
                    assert inner is outer
                    inner.execute(insert(categories_table).values(name=nested_category))

                active_after_inner.append(outer.in_transaction())
                in_request_count = outer.execute(
                    select(categories_table.c.id).where(categories_table.c.name.in_((outer_category, nested_category)))
                ).fetchall()
                assert len(in_request_count) == 2
                raise RuntimeError("force outer rollback")

    with db_core_transaction() as conn:
        persisted_count = conn.execute(
            select(categories_table.c.id).where(categories_table.c.name.in_((outer_category, nested_category)))
        ).fetchall()

    assert active_after_inner == [True]
    assert persisted_count == []


def test_core_transaction_commits_and_rolls_back(tmp_path):
    """Commit successful Core transactions and roll back failed ones."""
    database_path = tmp_path / "finscope.db"
    database_url = sqlite_database_url(database_path)

    with patch.object(engine_module, "settings", SimpleNamespace(database_url=database_url)):
        with db_core_transaction() as conn:
            conn.execute(text("CREATE TABLE sample (value TEXT NOT NULL)"))
            conn.execute(text("INSERT INTO sample (value) VALUES ('committed')"))

        with pytest.raises(RuntimeError):
            with db_core_transaction() as conn:
                conn.execute(text("INSERT INTO sample (value) VALUES ('rolled back')"))
                raise RuntimeError("force rollback")

    verification_engine = create_engine(database_url)
    try:
        with verification_engine.connect() as conn:
            values = [
                row._mapping["value"]
                for row in conn.execute(text("SELECT value FROM sample ORDER BY value")).fetchall()
            ]
    finally:
        verification_engine.dispose()

    assert values == ["committed"]


def test_core_transaction_with_external_transaction_does_not_commit_outer(tmp_path):
    """Verify an external transaction remains caller-owned after helper success."""
    database_url = sqlite_database_url(tmp_path / "finscope.db")
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE sample (value TEXT NOT NULL)"))

        with engine.connect() as conn:
            outer_transaction = conn.begin()
            conn.execute(text("INSERT INTO sample (value) VALUES ('outer')"))
            with db_core_transaction(conn=conn) as nested_conn:
                nested_conn.execute(text("INSERT INTO sample (value) VALUES ('nested')"))

            assert conn.in_transaction()
            outer_transaction.rollback()

        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM sample")).scalar_one() == 0
    finally:
        engine.dispose()


def test_core_transaction_with_external_transaction_rolls_back_only_savepoint(tmp_path):
    """Verify helper failures do not roll back a caller-owned transaction."""
    database_url = sqlite_database_url(tmp_path / "finscope.db")
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE sample (value TEXT NOT NULL)"))

        with engine.connect() as conn:
            outer_transaction = conn.begin()
            conn.execute(text("INSERT INTO sample (value) VALUES ('outer')"))

            with pytest.raises(RuntimeError):
                with db_core_transaction(conn=conn) as nested_conn:
                    nested_conn.execute(text("INSERT INTO sample (value) VALUES ('nested')"))
                    raise RuntimeError("force savepoint rollback")

            assert conn.in_transaction()
            conn.execute(text("INSERT INTO sample (value) VALUES ('after')"))
            outer_transaction.commit()

        with engine.connect() as conn:
            values = [
                row._mapping["value"]
                for row in conn.execute(text("SELECT value FROM sample ORDER BY value")).fetchall()
            ]
        assert values == ["after", "outer"]
    finally:
        engine.dispose()


def test_core_sqlite_connections_enforce_foreign_keys(tmp_path):
    """Enable SQLite foreign-key enforcement on pooled Core connections."""
    database_url = sqlite_database_url(tmp_path / "finscope.db")

    with patch.object(engine_module, "settings", SimpleNamespace(database_url=database_url)):
        with db_core_transaction() as conn:
            conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            conn.execute(text("""
                    CREATE TABLE child (
                        parent_id INTEGER NOT NULL,
                        FOREIGN KEY (parent_id) REFERENCES parent(id)
                    )
                    """))

        with pytest.raises(IntegrityError):
            with db_core_transaction() as conn:
                conn.execute(text("INSERT INTO child (parent_id) VALUES (42)"))


def test_init_db_uses_core_metadata_for_sqlite_url(tmp_path):
    """Initialize SQLite deployments through Core metadata and seed helpers."""
    database_path = tmp_path / "sqlite-init.db"
    test_settings = SimpleNamespace(
        database_url=sqlite_database_url(database_path),
        database_path=database_path,
    )

    with (
        patch.object(engine_module, "settings", test_settings),
        patch.object(
            connection_module.metadata, "create_all", wraps=connection_module.metadata.create_all
        ) as create_all,
    ):
        connection_module.init_db()
        engine = get_database_engine()
        inspector = inspect(engine)
        assert "transactions" in inspector.get_table_names()
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT builtin_key FROM categories WHERE name = 'UNKNOWN'")).scalar_one()
                == "unknown"
            )
            assert conn.execute(text("SELECT COUNT(*) FROM statement_types")).scalar_one() > 0
            assert conn.execute(text("SELECT COUNT(*) FROM categories")).scalar_one() > 0

    create_all.assert_called_once()
