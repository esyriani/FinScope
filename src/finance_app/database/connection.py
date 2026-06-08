"""Database initialization helpers.

Creates clean databases from SQLAlchemy Core metadata, validates existing
databases against the current schema, and seeds runtime defaults. Request and
transaction lifecycle helpers live in `finance_app.database.engine`.
"""

from sqlalchemy import inspect

from finance_app.database.engine import get_database_engine
from finance_app.database.seeds import (
    seed_category_taxonomy_defaults,
    seed_runtime_settings_defaults,
    seed_statement_type_defaults,
)
from finance_app.database.tables import metadata

RETIRED_SCHEMA_TABLES = {
    "settings",
    "schema_migrations",
    "app_metadata",
    "category_suggestions",
    "category_suggestion_tags",
    "merchant_normalization_cache",
    "merchant_normalization_review_queue",
}


def init_db():
    """Initialize the configured application database."""
    init_core_db()


def init_core_db(engine=None):
    """Initialize a current-schema database and seed runtime defaults.

    Empty databases are created from Core metadata. Existing FinScope databases
    are validated before seeding; they are not patched in place.
    """
    engine = engine or get_database_engine()
    if not database_has_existing_core_schema(engine):
        metadata.create_all(engine)

    with engine.begin() as conn:
        validate_core_schema(conn)
        seed_runtime_settings_defaults(conn)
        seed_statement_type_defaults(conn)
        seed_category_taxonomy_defaults(conn)


def database_has_existing_core_schema(engine):
    """Return whether the database already contains FinScope schema tables."""
    finscope_tables = set(metadata.tables) | RETIRED_SCHEMA_TABLES
    with engine.connect() as conn:
        existing_tables = set(inspect(conn).get_table_names())
    return bool(existing_tables & finscope_tables)


def validate_core_schema(conn):
    """Raise RuntimeError when an existing database is not the current schema.

    The validator checks table and column presence against SQLAlchemy Core
    metadata and rejects retired compatibility tables. It intentionally does
    not migrate or mutate existing schema objects.
    """
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    expected_tables = set(metadata.tables)
    retired_tables = sorted(existing_tables & RETIRED_SCHEMA_TABLES)
    missing_tables = sorted(expected_tables - existing_tables)
    missing_columns = {}

    for table_name, table in metadata.tables.items():
        if table_name not in existing_tables:
            continue

        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing = [column.name for column in table.columns if column.name not in actual_columns]
        if missing:
            missing_columns[table_name] = missing

    if retired_tables or missing_tables or missing_columns:
        raise RuntimeError(schema_validation_message(retired_tables, missing_tables, missing_columns))


def schema_validation_message(retired_tables, missing_tables, missing_columns):
    """Build a readable current-schema validation failure message."""
    details = []
    if missing_tables:
        details.append(f"missing tables: {', '.join(missing_tables)}")
    if missing_columns:
        formatted_columns = [
            f"{table}.{column}" for table, columns in sorted(missing_columns.items()) for column in columns
        ]
        details.append(f"missing columns: {', '.join(formatted_columns)}")
    if retired_tables:
        details.append(f"retired tables: {', '.join(retired_tables)}")

    return (
        "Configured database schema is not current. Recreate the development "
        "database or restore a current backup; " + "; ".join(details)
    )
