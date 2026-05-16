"""CSRF protection helpers."""

import hmac
import secrets

from flask import abort, jsonify, request, session


CSRF_SESSION_KEY = "_csrf_token"
CSRF_FIELD_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def csrf_token():
    """Return token."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf():
    """Validate csrf."""
    expected = session.get(CSRF_SESSION_KEY)
    submitted = request.headers.get(CSRF_HEADER_NAME)

    if not submitted:
        submitted = request.form.get(CSRF_FIELD_NAME)

    return bool(
        expected
        and submitted
        and hmac.compare_digest(str(expected), str(submitted))
    )


def register_csrf(app):
    """Register csrf."""
    app.jinja_env.globals[CSRF_FIELD_NAME] = csrf_token

    @app.context_processor
    def inject_csrf_token():
        """Handle inject CSRF token."""
        return {CSRF_FIELD_NAME: csrf_token}

    @app.before_request
    def protect_mutating_requests():
        """Handle protect mutating requests."""
        if request.method not in CSRF_METHODS:
            return None

        if validate_csrf():
            return None

        if request.is_json or request.headers.get("X-Requested-With") == "fetch":
            return jsonify({"ok": False, "message": "Invalid CSRF token."}), 403

        abort(403, description="Invalid CSRF token.")
