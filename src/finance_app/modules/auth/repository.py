"""Authentication persistence helpers.

Provides SQLAlchemy Core queries and write helpers for users, per-user
and audit events. Per-user setting queries live in the neutral users repository
so settings can share them without importing auth during app registration.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update

from finance_app.core.constants import USER_ROLE_OWNER
from finance_app.database.tables import (
    audit_log as audit_log_table,
)
from finance_app.database.tables import (
    users as users_table,
)
from finance_app.modules.users.repository import (
    USER_COLUMNS,
)


def owner_exists(conn: Any) -> bool:
    """Return whether an owner account exists in the current database."""
    return active_owner_count(conn) > 0


def active_owner_count(conn: Any) -> int:
    """Return the number of active owner accounts."""
    return conn.execute(
        select(func.count())
        .select_from(users_table)
        .where(
            users_table.c.role == USER_ROLE_OWNER,
            users_table.c.is_active == 1,
        )
    ).scalar_one()


def get_user_by_id(conn: Any, user_id: object) -> dict[str, Any] | None:
    """Return one user by primary key, or ``None`` when absent."""
    return conn.execute(select(*USER_COLUMNS).where(users_table.c.id == user_id)).mappings().fetchone()


def get_user_by_username(conn: Any, username: object) -> dict[str, Any] | None:
    """Return one user by case-insensitive username, or ``None`` when absent."""
    normalized = normalize_username_key(username)
    if not normalized:
        return None

    return conn.execute(select(*USER_COLUMNS).where(users_table.c.username_key == normalized)).mappings().fetchone()


def username_exists(conn: Any, username: object, exclude_user_id: object | None = None) -> bool:
    """Return whether a case-insensitive username already exists."""
    normalized = normalize_username_key(username)
    if not normalized:
        return False

    statement = select(users_table.c.id).where(users_table.c.username_key == normalized)
    if exclude_user_id is not None:
        statement = statement.where(users_table.c.id != exclude_user_id)

    return conn.execute(statement.limit(1)).fetchone() is not None


def insert_user(
    conn: Any,
    username: str,
    password_hash: str,
    role: str,
    must_change_password: bool,
    now: datetime,
    is_active: object = 1,
    display_name: object | None = None,
) -> int:
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


def update_display_name(conn: Any, user_id: object, display_name: str, now: datetime) -> int:
    """Update one user's UI display name and return the affected row count."""
    return conn.execute(
        update(users_table).where(users_table.c.id == user_id).values(display_name=display_name, updated_at=now)
    ).rowcount


def record_login_success(conn: Any, user_id: object, now: datetime) -> None:
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


def record_login_failure(
    conn: Any,
    user_id: object,
    failed_login_count: int,
    locked_until: datetime | None,
    now: datetime,
) -> None:
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


def update_password(conn: Any, user_id: object, password_hash: str, must_change_password: bool, now: datetime) -> int:
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


def update_user_active(conn: Any, user_id: object, is_active: bool, now: datetime) -> int:
    """Activate or deactivate one user and return the affected row count."""
    return conn.execute(
        update(users_table).where(users_table.c.id == user_id).values(is_active=1 if is_active else 0, updated_at=now)
    ).rowcount


def update_user_role(conn: Any, user_id: object, role: str, now: datetime) -> int:
    """Update one user's role and return the affected row count."""
    return conn.execute(
        update(users_table).where(users_table.c.id == user_id).values(role=role, updated_at=now)
    ).rowcount


def update_owner_roles_except(conn: Any, preserved_user_id: object, role: str, now: datetime) -> int:
    """Update all owner rows except the preserved user to the given role."""
    return conn.execute(
        update(users_table)
        .where(
            users_table.c.role == USER_ROLE_OWNER,
            users_table.c.id != preserved_user_id,
        )
        .values(role=role, updated_at=now)
    ).rowcount


def force_password_change(conn: Any, user_id: object, now: datetime) -> int:
    """Mark one user as requiring a password change at next login."""
    return conn.execute(
        update(users_table).where(users_table.c.id == user_id).values(must_change_password=1, updated_at=now)
    ).rowcount


def insert_audit_event(
    conn: Any,
    user_id: object | None,
    username: object | None,
    action: str,
    details: object | None = None,
    ip_address: object | None = None,
) -> None:
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


def normalize_username_key(username: object) -> str:
    """Return the lookup key produced by the database username_key column."""
    return str(username or "").strip().lower()


def normalize_display_name(display_name: object, fallback_username: object) -> str:
    """Return a non-empty display name for low-level insert helpers."""
    text = str(display_name or "").strip()
    return text or str(fallback_username or "").strip()
