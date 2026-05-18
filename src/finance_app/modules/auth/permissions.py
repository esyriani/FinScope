"""Authorization helpers for authenticated FinScope users.

Defines role permissions and reusable decorators used by controllers. The
helpers enforce backend authorization and return JSON errors for fetch-style
requests when appropriate.
"""

from functools import wraps

from flask import abort, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from finance_app.core.constants import (
    USER_ROLE_EDITOR,
    USER_ROLE_OWNER,
    USER_ROLE_VIEWER,
    normalize_user_role,
)


PERMISSION_MANAGE_USERS = "manage_users"
PERMISSION_MANAGE_GLOBAL_SETTINGS = "manage_global_settings"
PERMISSION_IMPORT_STATEMENTS = "import_statements"
PERMISSION_EDIT_TRANSACTIONS = "edit_transactions"
PERMISSION_MANAGE_RULES = "manage_rules"
PERMISSION_MANAGE_TAXONOMY = "manage_taxonomy"
PERMISSION_MANAGE_JOBS = "manage_jobs"
PERMISSION_EDIT_RECURRING = "edit_recurring"

OWNER_PERMISSIONS = frozenset(
    {
        PERMISSION_MANAGE_USERS,
        PERMISSION_MANAGE_GLOBAL_SETTINGS,
        PERMISSION_IMPORT_STATEMENTS,
        PERMISSION_EDIT_TRANSACTIONS,
        PERMISSION_MANAGE_RULES,
        PERMISSION_MANAGE_TAXONOMY,
        PERMISSION_MANAGE_JOBS,
        PERMISSION_EDIT_RECURRING,
    }
)
EDITOR_PERMISSIONS = frozenset(
    {
        PERMISSION_IMPORT_STATEMENTS,
        PERMISSION_EDIT_TRANSACTIONS,
        PERMISSION_MANAGE_RULES,
        PERMISSION_MANAGE_TAXONOMY,
        PERMISSION_MANAGE_JOBS,
        PERMISSION_EDIT_RECURRING,
    }
)
VIEWER_PERMISSIONS = frozenset()
ROLE_PERMISSIONS = {
    USER_ROLE_OWNER: OWNER_PERMISSIONS,
    USER_ROLE_EDITOR: EDITOR_PERMISSIONS,
    USER_ROLE_VIEWER: VIEWER_PERMISSIONS,
}
VIEWER_MUTATION_ENDPOINTS = {
    "auth.account",
    "auth.change_password",
    "auth.login",
    "auth.logout",
    "settings_page.settings_page",
}
PUBLIC_ENDPOINTS = {
    "auth.bootstrap",
    "auth.login",
    "static",
}
PASSWORD_CHANGE_ENDPOINTS = {
    "auth.change_password",
    "auth.logout",
    "static",
}


def current_user_can(permission):
    """Return whether the active user has the named permission."""
    if not getattr(current_user, "is_authenticated", False):
        return False
    return user_has_permission(current_user, permission)


def user_has_permission(user, permission):
    """Return whether a user-like object has the named permission."""
    role = normalize_user_role(getattr(user, "role", ""))
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def role_required(*roles):
    """Decorate a route so only authenticated users with one role can access it."""
    allowed_roles = set(roles)

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(*args, **kwargs):
            if normalize_user_role(getattr(current_user, "role", None)) not in allowed_roles:
                return forbidden_response()
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def permission_required(permission):
    """Decorate a route so only users with a permission can access it."""

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(*args, **kwargs):
            if not current_user_can(permission):
                return forbidden_response()
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def owner_required(view_func):
    """Decorate a route so only owners can access it."""
    return role_required(USER_ROLE_OWNER)(view_func)


def forbidden_response():
    """Return a 403 response matching the request style."""
    if request.is_json or request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": False, "message": "Forbidden."}), 403
    abort(403)


def register_authorization_guards(app):
    """Register cross-cutting authentication and authorization request guards.

    The guard keeps bootstrap public until an owner exists, protects every
    application route, forces password changes before regular app access, and
    blocks accidental viewer mutations even if a controller misses a decorator.
    """

    @app.before_request
    def enforce_authentication_state():
        """Protect app requests before controller code runs."""
        endpoint = request.endpoint or ""
        if endpoint == "static":
            return None

        from finance_app.modules.auth.service import has_owner_account

        if not has_owner_account():
            if endpoint == "auth.bootstrap":
                return None
            return redirect(url_for("auth.bootstrap"))

        if endpoint in PUBLIC_ENDPOINTS:
            return None

        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

        if current_user.must_change_password and endpoint not in PASSWORD_CHANGE_ENDPOINTS:
            return redirect(url_for("auth.change_password"))

        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and normalize_user_role(current_user.role) == USER_ROLE_VIEWER
            and endpoint not in VIEWER_MUTATION_ENDPOINTS
        ):
            return forbidden_response()

        return None
