"""Authentication persistence helpers.

Provides SQLAlchemy Core queries and write helpers for users, per-user
settings, and audit events. Callers own transaction boundaries through the
central database engine helpers.
"""

from sqlalchemy import delete, func, insert, select, update

from finance_app.core.constants import USER_ROLE_OWNER
from finance_app.database.tables import (
    audit_log as audit_log_table,
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


def count_users(conn):
    """Return the total number of user records."""
    return conn.execute(select(func.count()).select_from(users_table)).scalar_one()


def owner_exists(conn):
    """Return whether an owner account exists in the current database."""
    return active_owner_count(conn) > 0


def active_owner_count(conn):
    """Return the number of active owner accounts."""
    return conn.execute(
        select(func.count())
        .select_from(users_table)
        .where(
            users_table.c.role == USER_ROLE_OWNER,
            users_table.c.is_active == 1,
        )
    ).scalar_one()


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
    """Return all users ordered for the owner administration table."""
    return conn.execute(
        select(*USER_COLUMNS)
        .order_by(
            users_table.c.role == USER_ROLE_OWNER,
            func.lower(users_table.c.username),
            users_table.c.id,
        )
    ).mappings().fetchall()


def get_user_by_id(conn, user_id):
    """Return one user by primary key, or ``None`` when absent."""
    return conn.execute(
        select(*USER_COLUMNS).where(users_table.c.id == user_id)
    ).mappings().fetchone()


def get_user_by_username(conn, username):
    """Return one user by case-insensitive username, or ``None`` when absent."""
    normalized = normalize_username_key(username)
    if not normalized:
        return None

    return conn.execute(
        select(*USER_COLUMNS).where(func.lower(users_table.c.username) == normalized)
    ).mappings().fetchone()


def username_exists(conn, username, exclude_user_id=None):
    """Return whether a case-insensitive username already exists."""
    normalized = normalize_username_key(username)
    if not normalized:
        return False

    statement = select(users_table.c.id).where(func.lower(users_table.c.username) == normalized)
    if exclude_user_id is not None:
        statement = statement.where(users_table.c.id != exclude_user_id)

    return conn.execute(statement.limit(1)).fetchone() is not None


def insert_user(
    conn,
    username,
    password_hash,
    role,
    must_change_password,
    now,
    is_active=1,
    display_name=None,
):
    """Insert a user and return the new user ID."""
    display_value = normalize_display_name(display_name, username)
    result = conn.execute(
        insert(users_table).values(
            username=username,
            display_name=display_value,
            password_hash=password_hash,
            role=role,
            is_active=1 if is_active else 0,
            must_change_password=1 if must_change_password else 0,
            created_at=now,
            updated_at=now,
            failed_login_count=0,
        )
    )
    return result.inserted_primary_key[0]


def update_display_name(conn, user_id, display_name, now):
    """Update one user's UI display name and return the affected row count."""
    return conn.execute(
        update(users_table)
        .where(users_table.c.id == user_id)
        .values(display_name=display_name, updated_at=now)
    ).rowcount


def record_login_success(conn, user_id, now):
    """Reset failed login state and store the successful login timestamp."""
    conn.execute(
        update(users_table)
        .where(users_table.c.id == user_id)
        .values(
            failed_login_count=0,
            locked_until=None,
            last_login_at=now,
            updated_at=now,
        )
    )


def record_login_failure(conn, user_id, failed_login_count, locked_until, now):
    """Store failed login state after an unsuccessful authentication attempt."""
    conn.execute(
        update(users_table)
        .where(users_table.c.id == user_id)
        .values(
            failed_login_count=failed_login_count,
            locked_until=locked_until,
            updated_at=now,
        )
    )


def update_password(conn, user_id, password_hash, must_change_password, now):
    """Persist a password hash and password-change flag for one user."""
    return conn.execute(
        update(users_table)
        .where(users_table.c.id == user_id)
        .values(
            password_hash=password_hash,
            must_change_password=1 if must_change_password else 0,
            failed_login_count=0,
            locked_until=None,
            updated_at=now,
        )
    ).rowcount


def update_user_active(conn, user_id, is_active, now):
    """Activate or deactivate one user and return the affected row count."""
    return conn.execute(
        update(users_table)
        .where(users_table.c.id == user_id)
        .values(is_active=1 if is_active else 0, updated_at=now)
    ).rowcount


def update_user_role(conn, user_id, role, now):
    """Update one user's role and return the affected row count."""
    return conn.execute(
        update(users_table)
        .where(users_table.c.id == user_id)
        .values(role=role, updated_at=now)
    ).rowcount


def update_owner_roles_except(conn, preserved_user_id, role, now):
    """Update all owner rows except the preserved user to the given role."""
    return conn.execute(
        update(users_table)
        .where(
            users_table.c.role == USER_ROLE_OWNER,
            users_table.c.id != preserved_user_id,
        )
        .values(role=role, updated_at=now)
    ).rowcount


def force_password_change(conn, user_id, now):
    """Mark one user as requiring a password change at next login."""
    return conn.execute(
        update(users_table)
        .where(users_table.c.id == user_id)
        .values(must_change_password=1, updated_at=now)
    ).rowcount


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


def delete_user_setting(conn, user_id, key):
    """Delete one user-specific setting value."""
    conn.execute(
        delete(user_settings_table).where(
            user_settings_table.c.user_id == user_id,
            user_settings_table.c["key"] == key,
        )
    )


def insert_audit_event(conn, user_id, username, action, details=None, ip_address=None):
    """Append an audit event without storing sensitive values."""
    conn.execute(
        insert(audit_log_table).values(
            user_id=user_id,
            username=username,
            action=action,
            details=details,
            ip_address=ip_address,
        )
    )


def normalize_username_key(username):
    """Return a case-insensitive lookup key for usernames."""
    return str(username or "").strip().casefold()


def normalize_display_name(display_name, fallback_username):
    """Return a non-empty display name for low-level insert helpers."""
    text = str(display_name or "").strip()
    return text or str(fallback_username or "").strip()
