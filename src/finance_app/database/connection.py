"""Database initialization helpers.

Creates the configured schema from SQLAlchemy Core metadata and seeds runtime
defaults. Request and transaction lifecycle helpers live in
`finance_app.database.engine`.
"""

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
        seed_runtime_settings_defaults(conn)
        seed_statement_type_defaults(conn)
        seed_category_taxonomy_defaults(conn)
