"""Statement import type configuration helpers.

This module owns statement parser/import metadata stored in the
``statement_types`` table. Runtime user settings live in
``finance_app.modules.settings.runtime``.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import func, insert, select, update

from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPE_CREDIT_CARD,
    ACCOUNT_TYPES,
    STATEMENT_IMPORT_MODE_ENRICHMENT,
    STATEMENT_IMPORT_MODE_LEDGER,
    STATEMENT_IMPORT_MODES,
    STATEMENT_TYPE_PARSER_CREDIT_CARD,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
    STATEMENT_TYPE_PARSER_TYPES,
)
from finance_app.database.tables import normalize_name_key
from finance_app.database.tables import statement_types as statement_types_table
from finance_app.database.upsert import insert_or_select_unique_row


def get_statement_type_options(conn: Any, include_inactive: bool = False) -> list[Mapping[str, Any]]:
    """Return statement type options."""
    statement = select(
        statement_types_table.c.id,
        statement_types_table.c.name,
        statement_types_table.c.parser_type,
        statement_types_table.c.import_mode,
        statement_types_table.c.default_account_type,
        statement_types_table.c.active,
    ).order_by(func.lower(statement_types_table.c.name), statement_types_table.c.name)
    if not include_inactive:
        statement = statement.where(statement_types_table.c.active == 1)
    return conn.execute(statement).mappings().fetchall()


def get_statement_type_by_id(conn: Any, statement_type_id: object) -> Mapping[str, Any] | None:
    """Return statement type by ID."""
    try:
        parsed_id = int(str(statement_type_id))
    except (TypeError, ValueError):
        return None

    return (
        conn.execute(
            select(
                statement_types_table.c.id,
                statement_types_table.c.name,
                statement_types_table.c.parser_type,
                statement_types_table.c.import_mode,
                statement_types_table.c.default_account_type,
                statement_types_table.c.active,
            ).where(
                statement_types_table.c.id == parsed_id,
                statement_types_table.c.active == 1,
            )
        )
        .mappings()
        .fetchone()
    )


def get_statement_type_by_parser_type(conn: Any, parser_type: object) -> Mapping[str, Any] | None:
    """Return statement type by parser type."""
    normalized_parser_type = normalize_statement_parser_type(parser_type)
    return (
        conn.execute(
            select(
                statement_types_table.c.id,
                statement_types_table.c.name,
                statement_types_table.c.parser_type,
                statement_types_table.c.import_mode,
                statement_types_table.c.default_account_type,
                statement_types_table.c.active,
            )
            .where(
                statement_types_table.c.parser_type == normalized_parser_type,
                statement_types_table.c.active == 1,
            )
            .order_by(statement_types_table.c.id)
            .limit(1)
        )
        .mappings()
        .fetchone()
    )


def sync_statement_types(conn: Any, rows: Iterable[Mapping[str, Any]]) -> None:
    """Synchronize statement types."""
    cleaned_rows: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for row in rows:
        name = str(row.get("name") or "").strip()
        parser_type = normalize_statement_parser_type(row.get("parser_type"))
        import_mode = normalize_statement_import_mode(
            row.get("import_mode"),
            parser_type=parser_type,
        )
        default_account_type = normalize_default_account_type(
            row.get("default_account_type"),
            parser_type=parser_type,
        )
        if not name:
            continue

        normalized_name = normalize_name_key(name)
        if normalized_name in seen_names:
            raise ValueError("Statement type names must be unique.")
        seen_names.add(normalized_name)
        cleaned_rows.append(
            {
                "id": parse_optional_int(row.get("id")),
                "name": name,
                "parser_type": parser_type,
                "import_mode": import_mode,
                "default_account_type": default_account_type,
            }
        )

    if not cleaned_rows:
        raise ValueError("Add at least one statement type.")

    existing_ids = {
        row["id"]
        for row in conn.execute(select(statement_types_table.c.id).where(statement_types_table.c.active == 1))
        .mappings()
        .fetchall()
    }
    kept_ids: set[int] = set()

    for row in cleaned_rows:
        if row["id"] in existing_ids:
            conn.execute(
                update(statement_types_table)
                .where(statement_types_table.c.id == row["id"])
                .values(
                    name=row["name"],
                    parser_type=row["parser_type"],
                    import_mode=row["import_mode"],
                    default_account_type=row["default_account_type"],
                    active=1,
                )
            )
            kept_ids.add(row["id"])
        else:
            type_select = select(statement_types_table.c.id).where(
                statement_types_table.c.name_key == normalize_name_key(row["name"])
            )
            type_row = conn.execute(type_select).mappings().fetchone()
            if type_row is None:
                type_row, _ = insert_or_select_unique_row(
                    conn,
                    insert(statement_types_table).values(
                        name=row["name"],
                        parser_type=row["parser_type"],
                        import_mode=row["import_mode"],
                        default_account_type=row["default_account_type"],
                        active=1,
                    ),
                    type_select,
                )

            if type_row is not None:
                conn.execute(
                    update(statement_types_table)
                    .where(statement_types_table.c.id == type_row["id"])
                    .values(
                        parser_type=row["parser_type"],
                        import_mode=row["import_mode"],
                        default_account_type=row["default_account_type"],
                        active=1,
                    )
                )
                kept_ids.add(type_row["id"])

    retired_ids = existing_ids - kept_ids
    if retired_ids:
        conn.execute(update(statement_types_table).where(statement_types_table.c.id.in_(retired_ids)).values(active=0))


def normalize_statement_parser_type(value: object) -> str:
    """Normalize statement parser type."""
    text = str(value or "").strip()
    return text if text in STATEMENT_TYPE_PARSER_TYPES else STATEMENT_TYPE_PARSER_CREDIT_CARD


def normalize_statement_import_mode(value: object, parser_type: str | None = None) -> str:
    """Normalize statement import behavior."""
    if parser_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        return STATEMENT_IMPORT_MODE_ENRICHMENT
    text = str(value or "").strip()
    if text in STATEMENT_IMPORT_MODES:
        return text
    if parser_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        return STATEMENT_IMPORT_MODE_ENRICHMENT
    return STATEMENT_IMPORT_MODE_LEDGER


def normalize_default_account_type(value: object, parser_type: str | None = None) -> str:
    """Normalize the default account role for a statement type."""
    text = str(value or "").strip()
    if text in ACCOUNT_TYPES:
        return text
    if parser_type == STATEMENT_TYPE_PARSER_CREDIT_CARD:
        return ACCOUNT_TYPE_CREDIT_CARD
    return ACCOUNT_TYPE_CHECKING


def parse_optional_int(value: object) -> int | None:
    """Parse optional int."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
