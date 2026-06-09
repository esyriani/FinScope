"""SQLAlchemy engine and connection lifecycle helpers.

Provides the central SQLAlchemy Core engine factory, request-scoped
connections, and transaction helpers for configured database URLs.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from flask import g, has_request_context
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Connection, Engine, make_url

from finance_app.core.config import settings

CORE_DB_CONTEXT_KEY = "finance_core_db"
CORE_DB_TRANSACTION_DEPTH_KEY = "finance_core_transaction_depth"

_DATABASE_ENGINE: Engine | None = None
_DATABASE_ENGINE_URL: str | None = None
AUTO_CREATE_DATABASE_DIALECTS = {"mariadb", "mysql"}


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine for the configured database URL.

    Args:
        database_url: Optional SQLAlchemy database URL. When omitted, the
            application setting is used.

    Returns:
        A SQLAlchemy Engine suitable for Core metadata and expression usage.
    """
    database_url = database_url or settings.database_url
    ensure_database_exists(database_url)

    engine = create_engine(database_url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        register_sqlite_foreign_keys(engine)

    return engine


def ensure_database_exists(database_url: str) -> None:
    """Create the configured server-level database when the dialect requires it."""
    url = make_url(database_url)
    dialect_name = url.drivername.split("+", 1)[0].lower()
    if dialect_name not in AUTO_CREATE_DATABASE_DIALECTS or not url.database:
        return

    server_engine = create_engine(
        server_database_url(url),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        quoted_database = server_engine.dialect.identifier_preparer.quote_identifier(url.database)
        with server_engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE DATABASE IF NOT EXISTS "
                    f"{quoted_database} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        server_engine.dispose()


def server_database_url(url: URL) -> URL:
    """Return a URL that connects to the database server without a schema path."""
    return URL.create(
        drivername=url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        query=url.query,
    )


def register_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable SQLite foreign-key enforcement for new driver connections."""

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
        """Enable SQLite foreign keys on each pooled driver connection."""
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()


def get_database_engine() -> Engine:
    """Return the cached SQLAlchemy engine for the configured database URL."""
    global _DATABASE_ENGINE, _DATABASE_ENGINE_URL

    database_url = settings.database_url
    if _DATABASE_ENGINE is None or _DATABASE_ENGINE_URL != database_url:
        dispose_database_engine()
        _DATABASE_ENGINE = create_database_engine(database_url)
        _DATABASE_ENGINE_URL = database_url

    assert _DATABASE_ENGINE is not None
    return _DATABASE_ENGINE


def dispose_database_engine() -> None:
    """Dispose the cached SQLAlchemy engine, if one exists."""
    global _DATABASE_ENGINE, _DATABASE_ENGINE_URL

    if _DATABASE_ENGINE is not None:
        _DATABASE_ENGINE.dispose()
    _DATABASE_ENGINE = None
    _DATABASE_ENGINE_URL = None


def connect_core_db() -> Connection:
    """Open a SQLAlchemy Core connection from the configured engine."""
    return get_database_engine().connect()


def get_core_connection() -> Connection:
    """Return a SQLAlchemy Core connection for the current execution context."""
    if not has_request_context():
        return connect_core_db()

    conn = getattr(g, CORE_DB_CONTEXT_KEY, None)
    if conn is None or conn.closed:
        conn = connect_core_db()
        setattr(g, CORE_DB_CONTEXT_KEY, conn)

    return conn


def is_request_scoped_core_connection(conn: object) -> bool:
    """Return whether a Core connection is owned by the active Flask request."""
    return has_request_context() and getattr(g, CORE_DB_CONTEXT_KEY, None) is conn


def close_core_db(error: object | None = None) -> None:
    """Close the request-scoped SQLAlchemy Core connection, rolling back work."""
    del error
    conn = g.pop(CORE_DB_CONTEXT_KEY, None)
    if conn is None:
        return

    try:
        if conn.in_transaction():
            conn.rollback()
    finally:
        conn.close()


def register_core_db(app: Any) -> None:
    """Register SQLAlchemy Core database cleanup with a Flask application."""
    app.teardown_appcontext(close_core_db)


@contextmanager
def db_core_connection() -> Iterator[Connection]:
    """Yield a Core connection and close it when not request scoped.

    Runtime write paths should use db_core_transaction() so commits and
    rollbacks remain centralized.
    """
    conn = get_core_connection()
    try:
        yield conn
    finally:
        if not is_request_scoped_core_connection(conn):
            conn.close()


@contextmanager
def db_core_transaction(conn: Connection | None = None) -> Iterator[Connection]:
    """Yield a Core connection inside a managed transaction.

    When the connection is already inside either a database transaction or an
    outer logical db_core_transaction() block, the helper uses a savepoint and
    leaves the outer transaction under caller control. Connections opened by
    this helper keep the existing top-level commit-or-rollback behavior.
    """
    owns_connection = conn is None
    if conn is None:
        conn = get_core_connection()

    transaction_depth = conn.info.get(CORE_DB_TRANSACTION_DEPTH_KEY, 0)
    use_savepoint = transaction_depth > 0 or conn.in_transaction()
    conn.info[CORE_DB_TRANSACTION_DEPTH_KEY] = transaction_depth + 1
    try:
        if use_savepoint:
            with conn.begin_nested():
                yield conn
        else:
            try:
                yield conn
            except Exception:
                if conn.in_transaction():
                    conn.rollback()
                raise
            else:
                if conn.in_transaction():
                    conn.commit()
    finally:
        current_depth = conn.info.get(CORE_DB_TRANSACTION_DEPTH_KEY, 1)
        if current_depth <= 1:
            conn.info.pop(CORE_DB_TRANSACTION_DEPTH_KEY, None)
        else:
            conn.info[CORE_DB_TRANSACTION_DEPTH_KEY] = current_depth - 1
        if owns_connection and not is_request_scoped_core_connection(conn):
            conn.close()
