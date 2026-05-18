"""Authentication module integration.

Registers Flask-Login with Core-backed user loading and exposes the auth
blueprint for login, logout, bootstrap, password, and user-management routes.
"""

from flask_login import LoginManager

from finance_app.modules.auth.controller import auth_bp
from finance_app.modules.auth.permissions import register_authorization_guards
from finance_app.modules.auth.service import load_login_user


def register_auth(app):
    """Configure Flask-Login and global authentication guards for the app."""
    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to continue."
    login_manager.user_loader(load_login_user)
    login_manager.init_app(app)
    register_authorization_guards(app)
