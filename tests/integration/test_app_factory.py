"""Tests for Flask application factory wiring."""

from finance_app import create_app
from finance_app.database.engine import close_core_db


def test_create_app_registers_only_core_database_lifecycle():
    """Verify app startup registers Core cleanup without old cleanup hooks."""
    application = create_app()
    teardown_names = {func.__name__ for func in application.teardown_appcontext_funcs}

    assert close_core_db in application.teardown_appcontext_funcs
    assert "close_db" not in teardown_names
