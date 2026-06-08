"""Authentication and user-management services.

Coordinates password hashing, login state, lockout handling, owner bootstrap,
and owner-managed user changes on top of SQLAlchemy Core repositories.
"""

from datetime import datetime, timedelta, timezone
import secrets
import string

from sqlalchemy.exc import IntegrityError as SqlAlchemyIntegrityError
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError
from werkzeug.security import check_password_hash, generate_password_hash

from finance_app.core.constants import (
    USER_ROLE_EDITOR,
    USER_ROLE_OWNER,
    USER_ROLE_VIEWER,
    normalize_user_role,
)
from finance_app.database.dates import coerce_utc_datetime
from finance_app.database.engine import db_core_connection, db_core_transaction
from finance_app.modules.auth import repository
from finance_app.modules.auth.models import AuthenticatedUser

AUTH_OPERATIONAL_ERRORS = (SqlAlchemyOperationalError,)
AUTH_INTEGRITY_ERRORS = (SqlAlchemyIntegrityError,)
FAILED_LOGIN_LIMIT = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 10
MANAGED_USER_ROLES = (USER_ROLE_EDITOR, USER_ROLE_VIEWER)
DUMMY_PASSWORD_HASH = generate_password_hash("finscope-dummy-password")
TEMP_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_"


def has_owner_account():
    """Return whether the current database has an active owner account."""
    try:
        with db_core_connection() as conn:
            return repository.owner_exists(conn)
    except AUTH_OPERATIONAL_ERRORS:
        return False


def load_login_user(user_id):
    """Return a Flask-Login user object for an active user ID."""
    try:
        parsed_user_id = int(user_id)
    except (TypeError, ValueError):
        return None

    with db_core_connection() as conn:
        row = repository.get_user_by_id(conn, parsed_user_id)
    if row is None or not row["is_active"]:
        return None
    return AuthenticatedUser(row)


def authenticate_user(username, password, ip_address=None):
    """Authenticate credentials and update login tracking.

    Args:
        username: Submitted username.
        password: Submitted plaintext password.
        ip_address: Optional request IP address for audit logging.

    Returns:
        An ``AuthenticatedUser`` when credentials are valid and the account is
        active and not locked; otherwise ``None``. The caller must show a
        generic error for all failures.
    """
    submitted_password = str(password or "")
    with db_core_transaction() as conn:
        row = repository.get_user_by_username(conn, username)
        if row is None:
            check_password_hash(DUMMY_PASSWORD_HASH, submitted_password)
            return None

        now = utc_now()
        if not row["is_active"] or login_lock_active(row, now):
            repository.insert_audit_event(
                conn,
                row["id"],
                row["username"],
                "login_rejected",
                details="inactive_or_locked",
                ip_address=ip_address,
            )
            return None

        if check_password_hash(row["password_hash"], submitted_password):
            repository.record_login_success(conn, row["id"], now)
            repository.insert_audit_event(
                conn,
                row["id"],
                row["username"],
                "login_success",
                ip_address=ip_address,
            )
            refreshed = repository.get_user_by_id(conn, row["id"])
            return AuthenticatedUser(refreshed)

        failed_count = failed_count_after_attempt(row, now)
        locked_until = now + timedelta(minutes=LOCKOUT_MINUTES) if failed_count >= FAILED_LOGIN_LIMIT else None
        repository.record_login_failure(conn, row["id"], failed_count, locked_until, now)
        repository.insert_audit_event(
            conn,
            row["id"],
            row["username"],
            "login_failed",
            details="invalid_credentials",
            ip_address=ip_address,
        )
        return None


def bootstrap_owner(username, password, confirm_password, ip_address=None, display_name=None):
    """Create the first owner account for a database.

    Raises:
        ValueError: If an owner already exists or submitted fields are invalid.
    """
    normalized_username = clean_username(username)
    normalized_display_name = clean_display_name(display_name, normalized_username)
    validate_password_pair(password, confirm_password)

    try:
        with db_core_transaction() as conn:
            if repository.owner_exists(conn):
                raise ValueError("The owner account already exists.")
            if repository.username_exists(conn, normalized_username):
                raise ValueError("Username is already in use.")

            now = utc_now()
            user_id = repository.insert_user(
                conn,
                normalized_username,
                hash_password(password),
                USER_ROLE_OWNER,
                must_change_password=False,
                now=now,
                display_name=normalized_display_name,
            )
            from finance_app.modules.settings.runtime import seed_runtime_settings

            seed_runtime_settings(conn)
            repository.insert_audit_event(
                conn,
                user_id,
                normalized_username,
                "owner_bootstrap",
                ip_address=ip_address,
            )
            return repository.get_user_by_id(conn, user_id)
    except AUTH_INTEGRITY_ERRORS as exc:
        raise ValueError("The owner account already exists or username is already in use.") from exc


def create_managed_user(username, role, actor=None, ip_address=None, display_name=None):
    """Create an owner-managed editor or viewer with a temporary password."""
    normalized_username = clean_username(username)
    normalized_display_name = clean_display_name(display_name, normalized_username)
    normalized_role = clean_managed_role(role)
    temporary_password = generate_temporary_password()

    try:
        with db_core_transaction() as conn:
            if repository.username_exists(conn, normalized_username):
                raise ValueError("Username is already in use.")

            now = utc_now()
            user_id = repository.insert_user(
                conn,
                normalized_username,
                hash_password(temporary_password),
                normalized_role,
                must_change_password=True,
                now=now,
                display_name=normalized_display_name,
            )
            from finance_app.modules.settings.runtime import seed_runtime_settings

            seed_runtime_settings(conn)
            audit_actor_id, audit_actor_name = actor_identity(actor)
            repository.insert_audit_event(
                conn,
                audit_actor_id,
                audit_actor_name,
                "user_created",
                details=f"user_id={user_id};role={normalized_role}",
                ip_address=ip_address,
            )

            created_user = repository.get_user_by_id(conn, user_id)
    except AUTH_INTEGRITY_ERRORS as exc:
        raise ValueError("Username is already in use.") from exc

    return created_user, temporary_password


def set_user_active(user_id, is_active, actor=None, ip_address=None):
    """Activate or deactivate a user while preserving the last active owner."""
    with db_core_transaction() as conn:
        target = require_user(conn, user_id)
        if not is_active and target["role"] == USER_ROLE_OWNER and target["is_active"]:
            ensure_not_last_active_owner(conn)

        now = utc_now()
        repository.update_user_active(conn, target["id"], is_active, now)
        audit_actor_id, audit_actor_name = actor_identity(actor)
        repository.insert_audit_event(
            conn,
            audit_actor_id,
            audit_actor_name,
            "user_activated" if is_active else "user_deactivated",
            details=f"user_id={target['id']}",
            ip_address=ip_address,
        )


def change_user_role(user_id, role, actor=None, ip_address=None):
    """Change a managed user's role to editor or viewer.

    FinScope allows exactly one owner account, so owner promotion is not
    available through the user-management form and demoting the last active
    owner is rejected.
    """
    normalized_role = clean_managed_role(role)
    with db_core_transaction() as conn:
        target = require_user(conn, user_id)
        if target["role"] == USER_ROLE_OWNER:
            raise ValueError("Owner role cannot be changed.")

        now = utc_now()
        repository.update_user_role(conn, target["id"], normalized_role, now)
        audit_actor_id, audit_actor_name = actor_identity(actor)
        repository.insert_audit_event(
            conn,
            audit_actor_id,
            audit_actor_name,
            "user_role_changed",
            details=f"user_id={target['id']};role={normalized_role}",
            ip_address=ip_address,
        )


def reset_user_password(user_id, actor=None, ip_address=None):
    """Generate a temporary password and force a password change."""
    temporary_password = generate_temporary_password()
    with db_core_transaction() as conn:
        target = require_user(conn, user_id)
        if target["role"] == USER_ROLE_OWNER:
            raise ValueError("Owner password must be changed from the Account page.")
        now = utc_now()
        repository.update_password(
            conn,
            target["id"],
            hash_password(temporary_password),
            must_change_password=True,
            now=now,
        )
        audit_actor_id, audit_actor_name = actor_identity(actor)
        repository.insert_audit_event(
            conn,
            audit_actor_id,
            audit_actor_name,
            "user_password_reset",
            details=f"user_id={target['id']}",
            ip_address=ip_address,
        )
    return display_user_row(target), temporary_password


def hand_off_ownership(current_owner_id, target_user_id, actor=None, ip_address=None):
    """Transfer the unique owner role to an active non-owner user.

    The current owner is demoted to viewer in the same transaction so the
    database never persists two long-lived owner accounts. The target must
    already be active because ownership should not be handed to an account that
    cannot immediately sign in and manage access.
    """
    with db_core_transaction() as conn:
        current_owner = require_user(conn, current_owner_id)
        target = require_user(conn, target_user_id)
        if current_owner["role"] != USER_ROLE_OWNER or not current_owner["is_active"]:
            raise ValueError("Only the active owner can hand off ownership.")
        if target["id"] == current_owner["id"]:
            raise ValueError("Choose another active user to receive ownership.")
        if target["role"] == USER_ROLE_OWNER:
            raise ValueError("Choose a non-owner user to receive ownership.")
        if not target["is_active"]:
            raise ValueError("Ownership can only be handed off to an active user.")

        now = utc_now()
        repository.update_owner_roles_except(conn, target["id"], USER_ROLE_VIEWER, now)
        repository.update_user_role(conn, target["id"], USER_ROLE_OWNER, now)
        audit_actor_id, audit_actor_name = actor_identity(actor)
        repository.insert_audit_event(
            conn,
            audit_actor_id,
            audit_actor_name,
            "ownership_handoff",
            details=f"from_user_id={current_owner['id']};to_user_id={target['id']}",
            ip_address=ip_address,
        )
        return display_user_row(repository.get_user_by_id(conn, target["id"]))


def change_password(user_id, current_password, new_password, confirm_password, ip_address=None):
    """Change a user's own password after validating the current password."""
    validate_password_pair(new_password, confirm_password)
    with db_core_transaction() as conn:
        target = require_user(conn, user_id)
        if not check_password_hash(target["password_hash"], str(current_password or "")):
            raise ValueError("Current password is incorrect.")

        now = utc_now()
        repository.update_password(
            conn,
            target["id"],
            hash_password(new_password),
            must_change_password=False,
            now=now,
        )
        repository.insert_audit_event(
            conn,
            target["id"],
            target["username"],
            "password_changed",
            ip_address=ip_address,
        )


def list_managed_users():
    """Return all users for the owner user-management page."""
    with db_core_connection() as conn:
        return [display_user_row(row) for row in repository.list_users(conn)]


def update_own_display_name(user_id, display_name, actor=None, ip_address=None):
    """Update the authenticated user's display name."""
    normalized_display_name = clean_display_name(display_name)
    with db_core_transaction() as conn:
        target = require_user(conn, user_id)
        now = utc_now()
        repository.update_display_name(conn, target["id"], normalized_display_name, now)
        audit_actor_id, audit_actor_name = actor_identity(actor)
        repository.insert_audit_event(
            conn,
            audit_actor_id,
            audit_actor_name,
            "display_name_changed",
            details=f"username={target['username']}",
            ip_address=ip_address,
        )
        return repository.get_user_by_id(conn, target["id"])


def get_user_account(user_id):
    """Return one active user row for the Account page."""
    with db_core_connection() as conn:
        row = repository.get_user_by_id(conn, int(user_id))
    if row is None:
        raise ValueError("User not found.")
    return display_user_row(row)


def hash_password(password):
    """Return a Werkzeug scrypt password hash."""
    return generate_password_hash(str(password or ""), method="scrypt")


def validate_password_pair(password, confirm_password):
    """Validate a password and matching confirmation."""
    password_text = str(password or "")
    if password_text != str(confirm_password or ""):
        raise ValueError("Passwords do not match.")
    if len(password_text) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


def clean_username(username):
    """Return a validated username suitable for persistence."""
    text = str(username or "").strip()
    if len(text) < 3:
        raise ValueError("Username must be at least 3 characters.")
    if len(text) > 150:
        raise ValueError("Username must be 150 characters or fewer.")
    if any(character.isspace() for character in text):
        raise ValueError("Username cannot contain spaces.")
    return text


def clean_display_name(display_name, fallback=None):
    """Return a validated display name for UI presentation."""
    text = str(display_name or "").strip()
    if not text and fallback is not None:
        text = str(fallback or "").strip()
    if len(text) < 1:
        raise ValueError("Display name is required.")
    if len(text) > 150:
        raise ValueError("Display name must be 150 characters or fewer.")
    return text


def clean_managed_role(role):
    """Return a role owners may assign to managed users."""
    text = normalize_user_role(role)
    if text not in MANAGED_USER_ROLES:
        raise ValueError("Choose editor or viewer.")
    return text


def require_user(conn, user_id):
    """Return a user row or raise a validation error."""
    try:
        parsed_user_id = int(user_id)
    except (TypeError, ValueError):
        parsed_user_id = 0
    row = repository.get_user_by_id(conn, parsed_user_id)
    if row is None:
        raise ValueError("User not found.")
    return row


def ensure_not_last_active_owner(conn):
    """Raise when a requested change would remove the final active owner."""
    if repository.active_owner_count(conn) <= 1:
        raise ValueError("The last active owner cannot be changed.")


def generate_temporary_password(length=18):
    """Return a random temporary password suitable for first login."""
    return "".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(length))


def login_lock_active(user_row, now):
    """Return whether a user's temporary login lock is still active."""
    locked_until = parse_optional_datetime(user_row["locked_until"])
    return bool(locked_until and locked_until > now.replace(tzinfo=None))


def failed_count_after_attempt(user_row, now):
    """Return the failed-login count to persist after another failure."""
    locked_until = parse_optional_datetime(user_row["locked_until"])
    if locked_until and locked_until <= now.replace(tzinfo=None):
        return 1
    return int(user_row["failed_login_count"] or 0) + 1


def parse_optional_datetime(value):
    """Return a naive UTC datetime for a stored timestamp value."""
    if value in (None, ""):
        return None
    return coerce_utc_datetime(value)


def utc_now():
    """Return the current UTC datetime for persisted auth timestamps."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def actor_identity(actor):
    """Return audit identity fields for a Flask-Login user-like actor."""
    if actor is None or not getattr(actor, "is_authenticated", False):
        return None, None
    return int(actor.id), actor.username


def display_user_row(row):
    """Return a template-ready user row with normalized role text."""
    data = dict(row)
    data["role"] = normalize_user_role(data["role"])
    data["display_name"] = data.get("display_name") or data.get("username")
    return data
