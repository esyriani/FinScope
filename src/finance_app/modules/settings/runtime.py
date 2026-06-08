"""Runtime settings persistence helpers."""

from datetime import datetime, timezone

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError
from flask import has_request_context
from flask_login import current_user

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
    statement_types as statement_types_table,
    user_settings as user_settings_table,
)
from finance_app.database.upsert import insert_or_select_unique_row
from finance_app.modules.recurring.settings import RECURRENCE_DETECTION_DEFAULTS
from finance_app.modules.users import repository as user_repository


SETTINGS_DEFAULTS = {
    "default_table_page_size": str(settings.default_table_page_size),
    "comparison_max_years": str(settings.default_comparison_max_years),
    "comparison_insight_card_limit": str(settings.default_comparison_insight_card_limit),
    "home_top_category_limit": str(settings.default_home_top_category_limit),
    "merchant_table_limit": str(settings.default_merchant_table_limit),
    "rule_preview_limit": str(settings.default_rule_preview_limit),
    "rule_audit_transaction_limit": str(settings.default_rule_audit_transaction_limit),
    "theme_mode": THEME_MODE_DARK,
    "ui_language": normalize_language(settings.locale),
    "llm_confidence_threshold": str(settings.default_llm_confidence_threshold),
    "llm_review_threshold": str(settings.default_llm_review_threshold),
    "verify_threshold": str(settings.default_verify_threshold),
    "auto_llm_categorization_enabled": "0",
    "transaction_ai_rerun_enabled": "1" if settings.default_transaction_ai_rerun_enabled else "0",
    "openai_model": settings.default_categorization_model,
    "recurrence_minimum_occurrences": str(RECURRENCE_DETECTION_DEFAULTS.minimum_occurrences),
    "recurrence_date_tolerance_days": str(RECURRENCE_DETECTION_DEFAULTS.date_tolerance_days),
    "recurrence_amount_tolerance_absolute": str(RECURRENCE_DETECTION_DEFAULTS.amount_tolerance_absolute),
    "recurrence_amount_tolerance_percent": str(RECURRENCE_DETECTION_DEFAULTS.amount_tolerance_percent),
    "recurrence_missed_cycles_before_inactive": str(RECURRENCE_DETECTION_DEFAULTS.missed_cycles_before_inactive),
}

GENERAL_SETTING_KEYS = (
    "default_table_page_size",
    "comparison_max_years",
    "comparison_insight_card_limit",
    "home_top_category_limit",
    "merchant_table_limit",
    "rule_preview_limit",
    "rule_audit_transaction_limit",
    "theme_mode",
    "ui_language",
)
GLOBAL_SETTING_KEYS = ()
EDITABLE_SETTING_KEYS = tuple(SETTINGS_DEFAULTS.keys())
DATABASE_OPERATIONAL_ERRORS = (SqlAlchemyOperationalError,)


def seed_runtime_settings(conn):
    """Seed default settings for existing users without changing saved values."""
    for user in user_repository.list_users(conn):
        for key, value in SETTINGS_DEFAULTS.items():
            setting_select = select(user_settings_table.c.user_id).where(
                user_settings_table.c.user_id == user["id"],
                user_settings_table.c["key"] == key,
            )
            existing = conn.execute(setting_select).fetchone()
            if existing is None:
                insert_or_select_unique_row(
                    conn,
                    insert(user_settings_table).values(
                        user_id=user["id"],
                        key=key,
                        value=value,
                        updated_at=datetime.now(timezone.utc).replace(microsecond=0),
                    ),
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


def get_all_settings(conn, user_id=None):
    """Return settings for the resolved user, falling back to defaults."""
    values = dict(SETTINGS_DEFAULTS)
    active_user_id = resolve_settings_user_id(conn, user_id)
    if active_user_id is not None:
        try:
            user_values = user_repository.get_user_settings(conn, active_user_id)
        except DATABASE_OPERATIONAL_ERRORS:
            user_values = {}
        for key in EDITABLE_SETTING_KEYS:
            if key in user_values:
                values[key] = user_values[key]

    return values


def get_global_settings(conn):
    """Return the owner fallback settings for non-request callers."""
    return get_all_settings(conn)


def get_setting(conn, key, user_id=None):
    """Return one setting for the resolved user, falling back to defaults."""
    active_user_id = resolve_settings_user_id(conn, user_id)
    if active_user_id is not None:
        try:
            user_value = user_repository.get_user_setting(conn, active_user_id, key)
        except DATABASE_OPERATIONAL_ERRORS:
            user_value = None
        if user_value is not None:
            return user_value

    return SETTINGS_DEFAULTS.get(key)


def get_global_setting(conn, key):
    """Return one owner fallback setting for non-request callers."""
    return get_setting(conn, key)


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


def get_bool_setting(conn, key, fallback=False):
    """Return boolean setting from a stored runtime value."""
    value = get_setting(conn, key)
    if value is None:
        return bool(fallback)

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(fallback)


def get_unknown_category(conn):
    """Return the fixed built-in category used for uncategorized rows."""
    del conn
    return UNKNOWN_CATEGORY


def upsert_setting(conn, key, value):
    """Insert or update a setting for the resolved user."""
    user_id = resolve_settings_user_id(conn)
    if user_id is None:
        raise ValueError("No settings user is available.")
    upsert_user_setting(conn, user_id, key, value)


def upsert_user_setting(conn, user_id, key, value):
    """Insert or update one user-specific General setting."""
    user_repository.upsert_user_setting(
        conn,
        user_id,
        key,
        value,
        now=datetime.now(timezone.utc).replace(microsecond=0),
    )


def resolve_settings_user_id(conn, explicit_user_id=None):
    """Return the explicit, authenticated, or owner fallback user id."""
    if explicit_user_id is not None:
        try:
            return int(explicit_user_id)
        except (TypeError, ValueError):
            return None
    if has_request_context() and current_user.is_authenticated:
        return int(current_user.id)
    owner = user_repository.get_first_active_owner(conn)
    if owner is None:
        return None
    return int(owner["id"])


def current_user_id(explicit_user_id=None):
    """Return an explicit or authenticated user id without owner fallback."""
    if explicit_user_id is not None:
        try:
            return int(explicit_user_id)
        except (TypeError, ValueError):
            return None
    if not has_request_context() or not current_user.is_authenticated:
        return None
    return int(current_user.id)


