"""Database initialization helpers.

Creates the configured schema from SQLAlchemy Core metadata and seeds runtime
defaults. Request and transaction lifecycle helpers live in
`finance_app.database.engine`.
"""

from sqlalchemy import inspect, text

from finance_app.database.seeds import (
    seed_category_taxonomy_defaults,
    seed_runtime_settings_defaults,
    seed_statement_type_defaults,
)
from finance_app.database.engine import get_database_engine
from finance_app.database.tables import metadata


def init_db():
    """Initialize the configured application database."""
    init_core_db()


def init_core_db(engine=None):
    """Initialize a database from SQLAlchemy Core metadata and seed defaults."""
    engine = engine or get_database_engine()
    metadata.create_all(engine)
    with engine.begin() as conn:
        ensure_sqlite_schema_extensions(conn)
        seed_runtime_settings_defaults(conn)
        seed_statement_type_defaults(conn)
        seed_category_taxonomy_defaults(conn)


def ensure_sqlite_schema_extensions(conn):
    """Apply lightweight SQLite schema extensions needed before seed writes.

    SQLAlchemy's `create_all` creates the full schema for new databases but does
    not alter existing SQLite files. This keeps development databases usable when
    the categories table gains the built-in category key column.
    """
    if conn.dialect.name != "sqlite":
        return

    category_columns = {
        column["name"]
        for column in inspect(conn).get_columns("categories")
    }
    if "builtin_key" not in category_columns:
        conn.execute(text("ALTER TABLE categories ADD COLUMN builtin_key VARCHAR(64)"))
        conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS uq_categories_builtin_key ON categories (builtin_key)")
        )
