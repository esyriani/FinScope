"""Flask-Login user adapter.

Provides a lightweight authenticated user object backed by SQLAlchemy Core
rows. It deliberately does not introduce SQLAlchemy ORM state.
"""

from flask_login import UserMixin

from finance_app.core.constants import normalize_user_role


class AuthenticatedUser(UserMixin):
    """Represent the active Flask-Login user.

    Args:
        row: A mapping returned by the users repository.

    The object carries only session-facing identity and authorization fields;
    fresh user details should be read from the repository inside transactions.
    """

    def __init__(self, row):
        """Build a login user object from a persisted user row."""
        self.id = int(row["id"])
        self.username = row["username"]
        self.display_name = row["display_name"] or row["username"]
        self.role = normalize_user_role(row["role"])
        self.active = bool(row["is_active"])
        self.must_change_password = bool(row["must_change_password"])

    @property
    def is_active(self):
        """Return whether Flask-Login should treat the user as active."""
        return self.active

    def get_id(self):
        """Return the stable string identifier stored in the session."""
        return str(self.id)
