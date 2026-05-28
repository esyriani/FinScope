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
        ensure_statement_date_order_column(conn)
        seed_runtime_settings_defaults(conn)
        seed_statement_type_defaults(conn)
        seed_category_taxonomy_defaults(conn)


def ensure_statement_date_order_column(conn):
    """Add the persisted statement date-order option to existing databases."""
    columns = {
        column["name"]
        for column in inspect(conn).get_columns("statements")
    }
    if "date_order" in columns:
        return

    conn.execute(
        text(
            "ALTER TABLE statements "
            "ADD COLUMN date_order VARCHAR(32) NOT NULL DEFAULT 'auto'"
        )
    )
