"""Database seed orchestration helpers.

Seeds startup defaults through SQLAlchemy Core without importing feature
modules. Feature services may call these helpers when creating users or when
they need to reconcile default rows inside an existing transaction.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert, select, update

from finance_app.core.constants import DEFAULT_STATEMENT_TYPE_SEED_ROWS, USER_ROLE_OWNER
from finance_app.core.runtime_settings import EDITABLE_SETTING_KEYS, GENERAL_SETTING_KEYS, SETTINGS_DEFAULTS
from finance_app.database.tables import normalize_name_key
from finance_app.database.tables import statement_types as statement_types_table
from finance_app.database.tables import user_settings as user_settings_table
from finance_app.database.tables import users as users_table
from finance_app.database.taxonomy import seed_category_taxonomy
from finance_app.database.upsert import insert_or_select_unique_row


def seed_runtime_settings_defaults(conn: Any) -> None:
    """Seed default settings for existing users without changing saved values."""
    owner_id = resolve_owner_settings_user_id(conn)
    user_rows = conn.execute(select(users_table.c.id).order_by(users_table.c.id)).mappings().fetchall()
    for user in user_rows:
        user_id = int(user["id"])
        seeded_keys = GENERAL_SETTING_KEYS
        if owner_id is not None and user_id == owner_id:
            seeded_keys = EDITABLE_SETTING_KEYS
        for key in seeded_keys:
            value = SETTINGS_DEFAULTS[key]
            setting_select = select(user_settings_table.c.user_id).where(
                user_settings_table.c.user_id == user_id,
                user_settings_table.c["key"] == key,
            )
            existing = conn.execute(setting_select).fetchone()
            if existing is None:
                insert_or_select_unique_row(
                    conn,
                    insert(user_settings_table).values(
                        user_id=user_id,
                        key=key,
                        value=value,
                        updated_at=datetime.now(timezone.utc).replace(microsecond=0),
                    ),
                    setting_select,
                )


def resolve_owner_settings_user_id(conn: Any) -> int | None:
    """Return the active owner row id used for owner-managed default settings."""
    owner = (
        conn.execute(
            select(users_table.c.id)
            .where(
                users_table.c.role == USER_ROLE_OWNER,
                users_table.c.is_active == 1,
            )
            .order_by(users_table.c.id)
            .limit(1)
        )
        .mappings()
        .fetchone()
    )
    if owner is None:
        return None
    return int(owner["id"])


def seed_statement_type_defaults(conn: Any) -> None:
    """Seed statement type defaults."""
    for name, parser_type, import_mode, default_account_type in DEFAULT_STATEMENT_TYPE_SEED_ROWS:
        name_key = normalize_name_key(name)
        type_select = select(
            statement_types_table.c.id,
            statement_types_table.c.parser_type,
            statement_types_table.c.import_mode,
            statement_types_table.c.default_account_type,
        ).where(statement_types_table.c.name_key == name_key)
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


def seed_category_taxonomy_defaults(conn: Any) -> None:
    """Seed category taxonomy defaults."""
    seed_category_taxonomy(conn)
