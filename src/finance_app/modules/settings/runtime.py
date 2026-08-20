"""Runtime settings persistence helpers."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from flask import has_request_context
from flask_login import current_user  # type: ignore[import-untyped]
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError

from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.runtime_settings import (
    CONFIRM_AI_TOKEN_USAGE_SETTING_KEY,
    EDITABLE_SETTING_KEYS,
    GENERAL_SETTING_KEYS,
    GLOBAL_SETTING_KEYS,
    SETTINGS_DEFAULTS,
)
from finance_app.modules.users import repository as user_repository

DATABASE_OPERATIONAL_ERRORS: tuple[type[Exception], ...] = (SqlAlchemyOperationalError,)


def get_all_settings(conn: Any, user_id: object | None = None) -> dict[str, str]:
    """Return personal and owner-managed settings, falling back to defaults."""
    values = dict(SETTINGS_DEFAULTS)
    active_user_id = resolve_settings_user_id(conn, user_id)
    if active_user_id is not None:
        apply_setting_values(values, safe_user_settings(conn, active_user_id), GENERAL_SETTING_KEYS)

    owner_user_id = resolve_owner_settings_user_id(conn)
    if owner_user_id is not None:
        apply_setting_values(values, safe_user_settings(conn, owner_user_id), GLOBAL_SETTING_KEYS)

    return values


def get_global_settings(conn: Any) -> dict[str, str]:
    """Return owner-row settings, falling back to defaults."""
    values = dict(SETTINGS_DEFAULTS)
    owner_user_id = resolve_owner_settings_user_id(conn)
    if owner_user_id is not None:
        apply_setting_values(values, safe_user_settings(conn, owner_user_id), EDITABLE_SETTING_KEYS)
    return values


def get_setting(conn: Any, key: str, user_id: object | None = None) -> str | None:
    """Return one personal or owner-managed setting, falling back to defaults."""
    active_user_id = resolve_setting_user_id(conn, key, user_id)
    if active_user_id is not None:
        try:
            user_value = user_repository.get_user_setting(conn, active_user_id, key)
        except DATABASE_OPERATIONAL_ERRORS:
            user_value = None
        if user_value is not None:
            return user_value

    return SETTINGS_DEFAULTS.get(key)


def get_global_setting(conn: Any, key: str) -> str | None:
    """Return one owner-row setting, falling back to defaults."""
    owner_user_id = resolve_owner_settings_user_id(conn)
    if owner_user_id is not None:
        try:
            user_value = user_repository.get_user_setting(conn, owner_user_id, key)
        except DATABASE_OPERATIONAL_ERRORS:
            user_value = None
        if user_value is not None:
            return user_value
    return SETTINGS_DEFAULTS.get(key)


def safe_user_settings(conn: Any, user_id: int) -> dict[str, str]:
    """Return persisted settings for one user, or an empty mapping on startup races."""
    try:
        return user_repository.get_user_settings(conn, user_id)
    except DATABASE_OPERATIONAL_ERRORS:
        return {}


def apply_setting_values(values: dict[str, str], user_values: Mapping[str, str], keys: Iterable[str]) -> None:
    """Apply selected persisted keys to a settings dictionary."""
    for key in keys:
        if key in user_values:
            values[key] = user_values[key]


def get_int_setting(conn: Any, key: str, fallback: int) -> int:
    """Return int setting."""
    value = get_setting(conn, key)
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def get_float_setting(
    conn: Any,
    key: str,
    fallback: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Return float setting."""
    value = get_setting(conn, key)
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return fallback

    if minimum is not None and parsed < minimum:
        return fallback
    if maximum is not None and parsed > maximum:
        return fallback
    return parsed


def get_bool_setting(conn: Any, key: str, fallback: bool = False) -> bool:
    """Return boolean setting from a stored runtime value."""
    value = get_setting(conn, key)
    return parse_bool_setting_value(value, fallback=fallback)


def get_owner_bool_setting(conn: Any, key: str, fallback: bool = False) -> bool:
    """Return a boolean owner-managed setting from the owner fallback row."""
    value = get_global_setting(conn, key)
    return parse_bool_setting_value(value, fallback=fallback)


def parse_bool_setting_value(value: object, fallback: bool = False) -> bool:
    """Return a boolean interpretation of a persisted setting value."""
    if value is None:
        return bool(fallback)

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(fallback)


def confirm_ai_token_usage_enabled(conn: Any) -> bool:
    """Return whether AI actions must show and confirm a token estimate."""
    return get_owner_bool_setting(conn, CONFIRM_AI_TOKEN_USAGE_SETTING_KEY, True)


def get_unknown_category(conn: object) -> str:
    """Return the fixed built-in category used for uncategorized rows."""
    del conn
    return UNKNOWN_CATEGORY


def upsert_setting(conn: Any, key: str, value: object) -> None:
    """Insert or update a personal or owner-managed setting."""
    user_id = resolve_setting_user_id(conn, key)
    if user_id is None:
        raise ValueError("No settings user is available.")
    upsert_user_setting(conn, user_id, key, value)


def upsert_user_setting(conn: Any, user_id: int, key: str, value: object) -> None:
    """Insert or update one user-specific General setting."""
    user_repository.upsert_user_setting(
        conn,
        user_id,
        key,
        value,
        now=datetime.now(timezone.utc).replace(microsecond=0),
    )


def resolve_settings_user_id(conn: Any, explicit_user_id: object | None = None) -> int | None:
    """Return the explicit, authenticated, or owner fallback user id."""
    if explicit_user_id is not None:
        try:
            return int(str(explicit_user_id))
        except (TypeError, ValueError):
            return None
    if has_request_context() and current_user.is_authenticated:
        return int(current_user.id)
    owner = user_repository.get_first_active_owner(conn)
    if owner is None:
        return None
    return int(owner["id"])


def resolve_setting_user_id(conn: Any, key: str, explicit_user_id: object | None = None) -> int | None:
    """Return the persisted settings owner for one key."""
    if key in GLOBAL_SETTING_KEYS:
        return resolve_owner_settings_user_id(conn)
    return resolve_settings_user_id(conn, explicit_user_id)


def resolve_owner_settings_user_id(conn: Any) -> int | None:
    """Return the active owner settings row id, when one exists."""
    owner = user_repository.get_first_active_owner(conn)
    if owner is None:
        return None
    return int(owner["id"])


def current_user_id(explicit_user_id: object | None = None) -> int | None:
    """Return an explicit or authenticated user id without owner fallback."""
    if explicit_user_id is not None:
        try:
            return int(str(explicit_user_id))
        except (TypeError, ValueError):
            return None
    if not has_request_context() or not current_user.is_authenticated:
        return None
    return int(current_user.id)
