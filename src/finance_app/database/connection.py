"""Database initialization helpers.

Creates clean databases from SQLAlchemy Core metadata, validates existing
databases against the current schema, and seeds runtime defaults. Request and
transaction lifecycle helpers live in `finance_app.database.engine`.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Numeric, String, Text, UniqueConstraint, inspect
from sqlalchemy.types import Date, DateTime, Float, Integer, TypeDecorator

from finance_app.database.engine import get_database_engine
from finance_app.database.runtime_repair import repair_startup_runtime_state
from finance_app.database.seeds import (
    seed_category_taxonomy_defaults,
    seed_runtime_settings_defaults,
    seed_statement_type_defaults,
)
from finance_app.database.tables import metadata


def init_db() -> None:
    """Initialize the configured application database."""
    init_core_db()


def init_core_db(engine: Any | None = None) -> None:
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
        repair_startup_runtime_state(conn)


def database_has_existing_core_schema(engine: Any) -> bool:
    """Return whether the database already contains FinScope schema tables."""
    finscope_tables = set(metadata.tables)
    with engine.connect() as conn:
        existing_tables = set(inspect(conn).get_table_names())
    return bool(existing_tables & finscope_tables)


def validate_core_schema(conn: Any) -> None:
    """Raise RuntimeError when an existing database is not the current schema.

    The validator checks tables, columns, column definitions, constraints,
    foreign keys, and indexes against SQLAlchemy Core metadata. It intentionally
    does not migrate or mutate existing schema objects.
    """
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    expected_tables = set(metadata.tables)
    missing_tables = sorted(expected_tables - existing_tables)
    missing_columns: dict[str, list[str]] = {}
    schema_issues: dict[str, list[str]] = {}

    for table_name, table in metadata.tables.items():
        if table_name not in existing_tables:
            continue

        actual_column_rows = inspector.get_columns(table_name)
        actual_columns = {column["name"]: column for column in actual_column_rows}
        missing = [column.name for column in table.columns if column.name not in actual_columns]
        if missing:
            missing_columns[table_name] = missing
        validate_column_definitions(
            schema_issues,
            conn.dialect,
            table_name,
            table,
            actual_columns,
        )
        validate_primary_key(schema_issues, inspector, table_name, table)
        validate_unique_constraints(schema_issues, inspector, conn.dialect, table_name, table)
        validate_check_constraints(schema_issues, inspector, conn.dialect, table_name, table)
        validate_foreign_keys(schema_issues, inspector, conn.dialect, table_name, table)
        validate_indexes(schema_issues, inspector, conn.dialect, table_name, table)

    if missing_tables or missing_columns or schema_issues:
        raise RuntimeError(schema_validation_message(missing_tables, missing_columns, schema_issues))


def add_schema_issue(issues: dict[str, list[str]], label: str, detail: str) -> None:
    """Record one schema validation issue under a stable message label."""
    issues.setdefault(label, []).append(detail)


def validate_column_definitions(
    issues: dict[str, list[str]],
    dialect: Any,
    table_name: str,
    table: Any,
    actual_columns: Mapping[str, Mapping[str, Any]],
) -> None:
    """Compare reflected column definitions with Core metadata."""
    for expected_column in table.columns:
        actual_column = actual_columns.get(expected_column.name)
        if actual_column is None:
            continue

        expected_nullable = bool(expected_column.nullable)
        if bool(actual_column.get("nullable")) != expected_nullable:
            add_schema_issue(
                issues,
                "column mismatches",
                f"{table_name}.{expected_column.name} nullability",
            )

        expected_type = column_type_signature(expected_column.type, dialect)
        actual_type = column_type_signature(actual_column["type"], dialect)
        if actual_type != expected_type:
            add_schema_issue(
                issues,
                "column mismatches",
                (
                    f"{table_name}.{expected_column.name} type expected "
                    f"{format_type_signature(expected_type)}, found {format_type_signature(actual_type)}"
                ),
            )

        expected_computed = expected_column.computed
        actual_computed = actual_column.get("computed")
        if expected_computed is None:
            expected_default = normalize_default(expected_column.server_default)
            actual_default = normalize_default(actual_column.get("default"))
            if actual_default != expected_default:
                add_schema_issue(
                    issues,
                    "column mismatches",
                    f"{table_name}.{expected_column.name} default",
                )

        if expected_computed is None and actual_computed is not None:
            add_schema_issue(
                issues,
                "column mismatches",
                f"{table_name}.{expected_column.name} generated expression",
            )
        elif expected_computed is not None:
            validate_computed_column(
                issues,
                dialect,
                table_name,
                expected_column.name,
                expected_computed,
                actual_computed,
            )


def validate_computed_column(
    issues: dict[str, list[str]],
    dialect: Any,
    table_name: str,
    column_name: str,
    expected_computed: Any,
    actual_computed: Mapping[str, Any] | None,
) -> None:
    """Compare generated-column metadata when reflection exposes it."""
    if actual_computed is None:
        add_schema_issue(issues, "column mismatches", f"{table_name}.{column_name} generated expression")
        return

    expected_persisted = expected_computed.persisted
    actual_persisted = actual_computed.get("persisted")
    if actual_persisted is not None and expected_persisted is not None and bool(actual_persisted) != expected_persisted:
        add_schema_issue(issues, "column mismatches", f"{table_name}.{column_name} generated persistence")

    actual_sql = actual_computed.get("sqltext")
    if actual_sql and not sql_fragments_match(compile_sql(expected_computed.sqltext, dialect), actual_sql):
        add_schema_issue(issues, "column mismatches", f"{table_name}.{column_name} generated expression")


def validate_primary_key(issues: dict[str, list[str]], inspector: Any, table_name: str, table: Any) -> None:
    """Compare reflected primary-key columns with Core metadata."""
    expected_columns = [column.name for column in table.primary_key.columns]
    actual_columns = inspector.get_pk_constraint(table_name).get("constrained_columns") or []
    if actual_columns != expected_columns:
        add_schema_issue(issues, "primary key mismatches", f"{table_name} expected {expected_columns}")


def validate_unique_constraints(
    issues: dict[str, list[str]],
    inspector: Any,
    dialect: Any,
    table_name: str,
    table: Any,
) -> None:
    """Compare reflected unique constraints with Core metadata."""
    actual_constraints = {
        constraint["name"]: list(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint.get("name")
    }
    for constraint in table.constraints:
        if not isinstance(constraint, UniqueConstraint):
            continue
        expected_columns = [column.name for column in constraint.columns]
        actual_columns = reflected_name_lookup(actual_constraints, reflection_names(constraint, dialect, "constraint"))
        if actual_columns is None:
            add_schema_issue(issues, "missing unique constraints", f"{table_name}.{constraint.name}")
        elif actual_columns != expected_columns:
            add_schema_issue(issues, "unique constraint mismatches", f"{table_name}.{constraint.name}")


def validate_check_constraints(
    issues: dict[str, list[str]],
    inspector: Any,
    dialect: Any,
    table_name: str,
    table: Any,
) -> None:
    """Compare reflected check constraints with Core metadata."""
    actual_constraints = {
        constraint["name"]: normalize_sql(constraint.get("sqltext"))
        for constraint in inspector.get_check_constraints(table_name)
        if constraint.get("name")
    }
    for constraint in table.constraints:
        if not isinstance(constraint, CheckConstraint):
            continue
        actual_sql = reflected_name_lookup(actual_constraints, reflection_names(constraint, dialect, "constraint"))
        if actual_sql is None:
            add_schema_issue(issues, "missing check constraints", f"{table_name}.{constraint.name}")
            continue
        if not sql_fragments_match(compile_sql(constraint.sqltext, dialect), actual_sql):
            add_schema_issue(issues, "check constraint mismatches", f"{table_name}.{constraint.name}")


def validate_foreign_keys(
    issues: dict[str, list[str]],
    inspector: Any,
    dialect: Any,
    table_name: str,
    table: Any,
) -> None:
    """Compare reflected foreign keys and delete actions with Core metadata."""
    actual_foreign_keys = {
        foreign_key["name"]: reflected_foreign_key_signature(foreign_key)
        for foreign_key in inspector.get_foreign_keys(table_name)
        if foreign_key.get("name")
    }
    for constraint in table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        actual_signature = reflected_name_lookup(
            actual_foreign_keys,
            reflection_names(constraint, dialect, "constraint"),
        )
        expected_signature = foreign_key_constraint_signature(constraint)
        if actual_signature is None:
            add_schema_issue(issues, "missing foreign keys", f"{table_name}.{constraint.name}")
        elif actual_signature != expected_signature:
            add_schema_issue(issues, "foreign key mismatches", f"{table_name}.{constraint.name}")


def validate_indexes(issues: dict[str, list[str]], inspector: Any, dialect: Any, table_name: str, table: Any) -> None:
    """Compare reflected explicit indexes with Core metadata."""
    actual_indexes = {
        index["name"]: (list(index["column_names"]), bool(index.get("unique")))
        for index in inspector.get_indexes(table_name)
        if index.get("name")
    }
    for index in table.indexes:
        if not isinstance(index, Index):
            continue
        expected = ([column.name for column in index.columns], bool(index.unique))
        actual = reflected_name_lookup(actual_indexes, reflection_names(index, dialect, "index"))
        if actual is None:
            add_schema_issue(issues, "missing indexes", f"{table_name}.{index.name}")
        elif actual != expected:
            add_schema_issue(issues, "index mismatches", f"{table_name}.{index.name}")


def column_type_signature(column_type: Any, dialect: Any) -> tuple[Any, ...]:
    """Return a portable type signature for schema validation."""
    effective_type = effective_schema_type(column_type, dialect)
    if isinstance(effective_type, Numeric):
        return ("numeric", effective_type.precision, effective_type.scale)
    if isinstance(effective_type, Integer):
        return ("integer",)
    if isinstance(effective_type, Float):
        return ("float",)
    if isinstance(effective_type, Text):
        return ("text",)
    if isinstance(effective_type, String):
        return ("string", effective_type.length)
    if isinstance(effective_type, DateTime):
        return ("datetime",)
    if isinstance(effective_type, Date):
        return ("date",)
    return (effective_type.__class__.__name__.lower(),)


def effective_schema_type(column_type: Any, dialect: Any) -> Any:
    """Return the dialect-specific type used to create or reflect a column."""
    if isinstance(column_type, TypeDecorator):
        return column_type.load_dialect_impl(dialect)
    dialect_type = column_type.dialect_impl(dialect) if hasattr(column_type, "dialect_impl") else column_type
    if isinstance(dialect_type, TypeDecorator):
        return dialect_type.load_dialect_impl(dialect)
    return dialect_type


def format_type_signature(signature: tuple[Any, ...]) -> str:
    """Format a type signature for validation error messages."""
    if signature[0] == "string":
        return f"string({signature[1]})"
    if signature[0] == "numeric":
        return f"numeric({signature[1]}, {signature[2]})"
    return str(signature[0])


def foreign_key_constraint_signature(constraint: ForeignKeyConstraint) -> tuple[Any, ...]:
    """Return comparable foreign-key metadata from a Core constraint."""
    return (
        [element.parent.name for element in constraint.elements],
        constraint.elements[0].column.table.name,
        [element.column.name for element in constraint.elements],
        normalize_ondelete(constraint.elements[0].ondelete),
    )


def reflected_foreign_key_signature(foreign_key: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return comparable foreign-key metadata from reflected schema details."""
    return (
        list(foreign_key.get("constrained_columns") or []),
        foreign_key.get("referred_table"),
        list(foreign_key.get("referred_columns") or []),
        normalize_ondelete((foreign_key.get("options") or {}).get("ondelete")),
    )


def normalize_ondelete(value: object) -> str | None:
    """Return a normalized foreign-key delete action."""
    if value in (None, ""):
        return None
    return str(value).strip().upper()


def compile_sql(sql: Any, dialect: Any) -> str:
    """Compile a SQLAlchemy expression into a literal SQL fragment."""
    return str(sql.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def reflection_names(schema_item: Any, dialect: Any, item_type: str) -> list[str]:
    """Return logical and dialect-rendered names used by schema reflection."""
    names = [str(schema_item.name)] if schema_item.name else []
    preparer = getattr(dialect, "identifier_preparer", None)
    formatter_name = "format_index" if item_type == "index" else "format_constraint"
    formatter = getattr(preparer, formatter_name, None)
    if formatter is not None:
        try:
            rendered_name = formatter(schema_item)
        except Exception:
            rendered_name = None
        if rendered_name:
            unquoted_name = strip_identifier_quotes(rendered_name)
            if unquoted_name not in names:
                names.append(unquoted_name)
    return names


def reflected_name_lookup(mapping: Mapping[str, Any], names: Sequence[str]) -> Any | None:
    """Return a reflected object by any acceptable physical or logical name."""
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def strip_identifier_quotes(value: object) -> str:
    """Remove dialect identifier quotes from a reflected object name."""
    return str(value).replace("`", "").replace('"', "")


def sql_fragments_match(expected: object, actual: object) -> bool:
    """Return whether two reflected SQL fragments describe the same invariant."""
    return normalize_sql(expected) == normalize_sql(actual) or _normalize_sql(expected, loose=True) == _normalize_sql(
        actual,
        loose=True,
    )


def normalize_sql(value: object) -> str:
    """Normalize reflected SQL fragments for portable comparison."""
    return _normalize_sql(value, loose=False)


def _normalize_sql(value: object, *, loose: bool) -> str:
    """Normalize reflected SQL fragments for portable comparison."""
    if value in (None, ""):
        return ""
    normalized = strip_identifier_quotes(value).replace('"', "'")
    normalized = re.sub(r"\bcurrent_timestamp\s*\(\s*\)", "current_timestamp", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\blcase\s*\(", "lower(", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\boctet_length\s*\(", "length(", normalized, flags=re.IGNORECASE)
    normalized = " ".join(normalized.strip().split())
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    normalized = normalized.replace("!=", "<>")
    normalized = normalized.lower()
    if loose:
        normalized = normalized.replace("(", "").replace(")", "")
    return re.sub(r"\s+", "", normalized)


def normalize_default(value: object) -> str | None:
    """Normalize reflected and metadata default expressions."""
    if value is None:
        return None
    default = value.arg if hasattr(value, "arg") else value
    normalized = str(default).strip()
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    normalized = re.sub(r"\bcurrent_timestamp\s*\(\s*\)", "current_timestamp", normalized, flags=re.IGNORECASE)
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == "'":
        normalized = normalized[1:-1]
    return normalized.lower()


def schema_validation_message(
    missing_tables: Sequence[str],
    missing_columns: Mapping[str, Sequence[str]],
    schema_issues: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """Build a readable current-schema validation failure message."""
    details: list[str] = []
    if missing_tables:
        details.append(f"missing tables: {', '.join(missing_tables)}")
    if missing_columns:
        formatted_columns = [
            f"{table}.{column}" for table, columns in sorted(missing_columns.items()) for column in columns
        ]
        details.append(f"missing columns: {', '.join(formatted_columns)}")
    for label, issues in (schema_issues or {}).items():
        if issues:
            details.append(f"{label}: {', '.join(sorted(issues))}")

    return (
        "Configured database schema is not current. Recreate the development "
        "database or restore a current backup; " + "; ".join(details)
    )
