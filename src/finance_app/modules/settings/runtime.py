"""Runtime settings persistence helpers."""

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError

from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPE_CREDIT_CARD,
    ACCOUNT_TYPES,
    DEFAULT_STATEMENT_TYPE_SEED_ROWS,
    STATEMENT_IMPORT_MODE_ENRICHMENT,
    STATEMENT_IMPORT_MODE_LEDGER,
    STATEMENT_IMPORT_MODES,
    STATEMENT_TYPE_PARSER_CREDIT_CARD,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
    STATEMENT_TYPE_PARSER_TYPES,
    THEME_MODE_DARK,
    UNKNOWN_CATEGORY,
)
from finance_app.core.config import settings
from finance_app.core.i18n import normalize_language
from finance_app.database.engine import db_core_connection
from finance_app.database.tables import (
    settings as settings_table,
    statement_types as statement_types_table,
)
from finance_app.database.upsert import insert_or_select_unique_row
from finance_app.modules.recurring.settings import RECURRENCE_DETECTION_DEFAULTS


SETTINGS_DEFAULTS = {
    "default_table_page_size": str(settings.default_table_page_size),
    "comparison_max_years": str(settings.default_comparison_max_years),
    "home_top_category_limit": str(settings.default_home_top_category_limit),
    "merchant_table_limit": str(settings.default_merchant_table_limit),
    "rule_preview_limit": str(settings.default_rule_preview_limit),
    "theme_mode": THEME_MODE_DARK,
    "ui_language": normalize_language(settings.locale),
    "llm_confidence_threshold": str(settings.default_llm_confidence_threshold),
    "verify_threshold": str(settings.default_verify_threshold),
    "openai_model": settings.default_categorization_model,
    "recurrence_minimum_occurrences": str(RECURRENCE_DETECTION_DEFAULTS.minimum_occurrences),
    "recurrence_date_tolerance_days": str(RECURRENCE_DETECTION_DEFAULTS.date_tolerance_days),
    "recurrence_amount_tolerance_absolute": str(RECURRENCE_DETECTION_DEFAULTS.amount_tolerance_absolute),
    "recurrence_amount_tolerance_percent": str(RECURRENCE_DETECTION_DEFAULTS.amount_tolerance_percent),
    "recurrence_missed_cycles_before_inactive": str(RECURRENCE_DETECTION_DEFAULTS.missed_cycles_before_inactive),
}

EDITABLE_SETTING_KEYS = tuple(SETTINGS_DEFAULTS.keys())
DATABASE_OPERATIONAL_ERRORS = (SqlAlchemyOperationalError,)


def seed_runtime_settings(conn):
    """Seed runtime settings."""
    rows = [(key, value) for key, value in SETTINGS_DEFAULTS.items()]
    for key, value in rows:
        setting_select = select(settings_table.c["key"]).where(settings_table.c["key"] == key)
        existing = conn.execute(setting_select).fetchone()
        if existing is None:
            insert_or_select_unique_row(
                conn,
                insert(settings_table).values(key=key, value=value),
                setting_select,
            )


def seed_statement_types(conn):
    """Seed statement types."""
    for name, parser_type, import_mode, default_account_type in DEFAULT_STATEMENT_TYPE_SEED_ROWS:
        type_select = select(
            statement_types_table.c.id,
            statement_types_table.c.parser_type,
            statement_types_table.c.import_mode,
            statement_types_table.c.default_account_type,
        ).where(statement_types_table.c.name == name)
        existing = conn.execute(type_select).mappings().fetchone()
        if existing is None:
            existing, inserted = insert_or_select_unique_row(
                conn,
                insert(statement_types_table).values(
                    name=name,
                    parser_type=parser_type,
                    import_mode=import_mode,
                    default_account_type=default_account_type,
                    active=1,
                ),
                type_select,
            )
            if inserted:
                continue

        if existing is not None:
            conn.execute(
                update(statement_types_table)
                .where(statement_types_table.c.id == existing["id"])
                .values(
                    parser_type=existing["parser_type"] or parser_type,
                    import_mode=existing["import_mode"] or import_mode,
                    default_account_type=existing["default_account_type"] or default_account_type,
                    active=1,
                )
            )


def get_statement_type_options(conn, include_inactive=False):
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


def get_statement_type_by_id(conn, statement_type_id):
    """Return statement type by ID."""
    try:
        parsed_id = int(statement_type_id)
    except (TypeError, ValueError):
        return None

    return conn.execute(
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
    ).mappings().fetchone()


def get_statement_type_by_parser_type(conn, parser_type):
    """Return statement type by parser type."""
    normalized_parser_type = normalize_statement_parser_type(parser_type)
    return conn.execute(
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
    ).mappings().fetchone()


def sync_statement_types(conn, rows):
    """Synchronize statement types."""
    cleaned_rows = []
    seen_names = set()

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

        normalized_name = name.casefold()
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
        for row in conn.execute(
            select(statement_types_table.c.id).where(statement_types_table.c.active == 1)
        ).mappings().fetchall()
    }
    kept_ids = set()

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
            type_select = select(statement_types_table.c.id).where(statement_types_table.c.name == row["name"])
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
        conn.execute(
            update(statement_types_table)
            .where(statement_types_table.c.id.in_(retired_ids))
            .values(active=0)
        )


def normalize_statement_parser_type(value):
    """Normalize statement parser type."""
    text = str(value or "").strip()
    return text if text in STATEMENT_TYPE_PARSER_TYPES else STATEMENT_TYPE_PARSER_CREDIT_CARD


def normalize_statement_import_mode(value, parser_type=None):
    """Normalize statement import behavior."""
    if parser_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        return STATEMENT_IMPORT_MODE_ENRICHMENT
    text = str(value or "").strip()
    if text in STATEMENT_IMPORT_MODES:
        return text
    if parser_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        return STATEMENT_IMPORT_MODE_ENRICHMENT
    return STATEMENT_IMPORT_MODE_LEDGER


def normalize_default_account_type(value, parser_type=None):
    """Normalize the default account role for a statement type."""
    text = str(value or "").strip()
    if text in ACCOUNT_TYPES:
        return text
    if parser_type == STATEMENT_TYPE_PARSER_CREDIT_CARD:
        return ACCOUNT_TYPE_CREDIT_CARD
    return ACCOUNT_TYPE_CHECKING


def parse_optional_int(value):
    """Parse optional int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_all_settings(conn):
    """Return all settings."""
    try:
        rows = conn.execute(
            select(settings_table.c["key"], settings_table.c.value)
        ).mappings().fetchall()
    except DATABASE_OPERATIONAL_ERRORS:
        rows = []

    values = {row["key"]: row["value"] for row in rows}
    for key, default_value in SETTINGS_DEFAULTS.items():
        values.setdefault(key, default_value)
    return values


def get_setting(conn, key):
    """Return setting."""
    try:
        row = conn.execute(
            select(settings_table.c.value).where(settings_table.c["key"] == key)
        ).mappings().fetchone()
    except DATABASE_OPERATIONAL_ERRORS:
        row = None
    if row is None:
        return SETTINGS_DEFAULTS.get(key)
    return row["value"]


def get_setting_with_fallback(key, fallback_value):
    """Return setting with fallback."""
    with db_core_connection() as conn:
        try:
            value = get_setting(conn, key)
        except DATABASE_OPERATIONAL_ERRORS:
            value = fallback_value

    if value is None:
        return fallback_value
    return value


def get_int_setting(conn, key, fallback):
    """Return int setting."""
    value = get_setting(conn, key)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def get_float_setting(conn, key, fallback, minimum=None, maximum=None):
    """Return float setting."""
    value = get_setting(conn, key)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback

    if minimum is not None and parsed < minimum:
        return fallback
    if maximum is not None and parsed > maximum:
        return fallback
    return parsed


def get_unknown_category(conn):
    """Return the fixed built-in category used for uncategorized rows."""
    del conn
    return UNKNOWN_CATEGORY


def upsert_setting(conn, key, value):
    """Insert or update setting."""
    setting_select = select(settings_table.c["key"]).where(settings_table.c["key"] == key)
    existing = conn.execute(setting_select).fetchone()
    if existing is None:
        insert_or_select_unique_row(
            conn,
            insert(settings_table).values(key=key, value=str(value)),
            setting_select,
        )

    conn.execute(
        update(settings_table)
        .where(settings_table.c["key"] == key)
        .values(value=str(value))
    )


