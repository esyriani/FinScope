"""Shared user repository helpers.

Provides SQLAlchemy Core queries for user rows and per-user runtime settings.
The helpers depend only on database tables and constants so auth and settings
modules can share them without creating registration-time import cycles.
"""

from sqlalchemy import func, insert, select, update

from finance_app.core.constants import USER_ROLE_OWNER
from finance_app.database.tables import (
    user_settings as user_settings_table,
    users as users_table,
)


USER_COLUMNS = (
    users_table.c.id,
    users_table.c.username,
    users_table.c.display_name,
    users_table.c.password_hash,
    users_table.c.role,
    users_table.c.is_active,
    users_table.c.must_change_password,
    users_table.c.created_at,
    users_table.c.updated_at,
    users_table.c.last_login_at,
    users_table.c.failed_login_count,
    users_table.c.locked_until,
)


def get_first_active_owner(conn):
    """Return the active owner row used for non-request settings fallback."""
    return conn.execute(
        select(*USER_COLUMNS)
        .where(
            users_table.c.role == USER_ROLE_OWNER,
            users_table.c.is_active == 1,
        )
        .order_by(users_table.c.id)
        .limit(1)
    ).mappings().fetchone()


def list_users(conn):
    """Return all users ordered for owner administration and setting seeding."""
    return conn.execute(
        select(*USER_COLUMNS)
        .order_by(
            users_table.c.role == USER_ROLE_OWNER,
            func.lower(users_table.c.username),
            users_table.c.id,
        )
    ).mappings().fetchall()


def get_user_settings(conn, user_id):
    """Return persisted settings for one user as a key/value mapping."""
    rows = conn.execute(
        select(user_settings_table.c["key"], user_settings_table.c.value)
        .where(user_settings_table.c.user_id == user_id)
    ).mappings().fetchall()
    return {row["key"]: row["value"] for row in rows}


def get_user_setting(conn, user_id, key):
    """Return one persisted user setting value, or ``None`` when absent."""
    row = conn.execute(
        select(user_settings_table.c.value).where(
            user_settings_table.c.user_id == user_id,
            user_settings_table.c["key"] == key,
        )
    ).mappings().fetchone()
    return None if row is None else row["value"]


def upsert_user_setting(conn, user_id, key, value, now):
    """Insert or update a user-specific setting value."""
    existing = conn.execute(
        select(user_settings_table.c.user_id).where(
            user_settings_table.c.user_id == user_id,
            user_settings_table.c["key"] == key,
        )
    ).fetchone()
    if existing is None:
        conn.execute(
            insert(user_settings_table).values(
                user_id=user_id,
                key=key,
                value=str(value),
                updated_at=now,
            )
        )
        return

    conn.execute(
        update(user_settings_table)
        .where(
            user_settings_table.c.user_id == user_id,
            user_settings_table.c["key"] == key,
        )
        .values(value=str(value), updated_at=now)
    )
