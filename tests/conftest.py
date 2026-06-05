"""Shared pytest fixtures for application and database tests."""

import sys
import shutil
from dataclasses import replace
from pathlib import Path

sys.dont_write_bytecode = True

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finance_app as app_package  # noqa: E402
from finance_app.core.constants import USER_ROLE_EDITOR, USER_ROLE_VIEWER  # noqa: E402
from finance_app.core import config as config_module  # noqa: E402
from finance_app.database import connection as connection_module  # noqa: E402
from finance_app.database import engine as engine_module  # noqa: E402
from finance_app.modules.auth import repository as auth_repository  # noqa: E402
from finance_app.modules.auth.service import bootstrap_owner, hash_password, utc_now  # noqa: E402
from tests.support.database import TestDataFactory  # noqa: E402
from tests.support.network import install_network_guard  # noqa: E402
from tests.support.web import CsrfEnabledClient  # noqa: E402


LAYER_MARKERS = {
    "unit": "unit",
    "integration": "integration",
    "routes": "route",
    "smoke": "smoke",
}
DB_FIXTURES = {"core_conn", "data_factory"}
FLASK_FIXTURES = {
    "app",
    "client",
    "owner_client",
    "editor_client",
    "viewer_client",
    "anonymous_client",
    "stale_session_client",
    "must_change_password_client",
    "csrf_client",
    "anonymous_csrf_client",
    "runner",
}
TEST_OWNER_USERNAME = "owner"
TEST_OWNER_PASSWORD = "OwnerPass123!"
TEST_EDITOR_USERNAME = "fixture_editor"
TEST_VIEWER_USERNAME = "fixture_viewer"
TEST_MUST_CHANGE_USERNAME = "fixture_must_change"
TEST_USER_PASSWORD = "FixturePass123!"


def remove_python_bytecode(root):
    """Remove python bytecode."""
    for pycache_dir in root.rglob("__pycache__"):
        if pycache_dir.is_dir():
            shutil.rmtree(pycache_dir, ignore_errors=True)

    for bytecode_file in root.rglob("*.py[co]"):
        bytecode_file.unlink(missing_ok=True)


def pytest_configure(config):
    """Handle pytest configure."""
    sys.dont_write_bytecode = True
    remove_python_bytecode(ROOT / "src")
    remove_python_bytecode(ROOT / "tests")


def pytest_sessionfinish(session, exitstatus):
    """Handle pytest sessionfinish."""
    remove_python_bytecode(ROOT / "src")
    remove_python_bytecode(ROOT / "tests")


def pytest_collection_modifyitems(config, items):
    """Apply layer and capability markers from the tests directory layout."""
    tests_root = ROOT / "tests"

    for item in items:
        path = Path(str(item.fspath)).resolve()
        try:
            relative_parts = path.relative_to(tests_root).parts
        except ValueError:
            continue

        layer = relative_parts[0] if relative_parts else ""
        marker_name = LAYER_MARKERS.get(layer)
        if marker_name:
            item.add_marker(getattr(pytest.mark, marker_name))

        if layer == "smoke":
            item.add_marker(pytest.mark.slow)

        fixture_names = set(getattr(item, "fixturenames", ()))
        if fixture_names & DB_FIXTURES:
            item.add_marker(pytest.mark.db)
        if fixture_names & FLASK_FIXTURES:
            item.add_marker(pytest.mark.flask)


def initialize_test_database(database_path):
    """Create the full application schema through the production init path."""
    del database_path
    connection_module.init_core_db()


def seed_test_owner():
    """Create the default authenticated owner used by existing route tests."""
    with engine_module.db_core_transaction() as conn:
        if auth_repository.owner_exists(conn):
            return auth_repository.get_user_by_username(conn, TEST_OWNER_USERNAME)

    return bootstrap_owner(TEST_OWNER_USERNAME, TEST_OWNER_PASSWORD, TEST_OWNER_PASSWORD)


def get_or_create_test_user(username, role, *, must_change_password=False):
    """Return an active user row for a named test role.

    Args:
        username: Stable username for the fixture-owned user.
        role: Persisted application role.
        must_change_password: Whether the user should be forced into the
            password-change flow.

    Returns:
        A mapping row for the existing or newly inserted user. The helper opens
        its own transaction so client fixtures do not need the database fixture.
    """
    with engine_module.db_core_transaction() as conn:
        existing = auth_repository.get_user_by_username(conn, username)
        if existing is not None:
            return existing

        now = utc_now()
        user_id = auth_repository.insert_user(
            conn,
            username,
            hash_password(TEST_USER_PASSWORD),
            role,
            must_change_password=must_change_password,
            now=now,
            display_name=username.replace("_", " ").title(),
        )
        return auth_repository.get_user_by_id(conn, user_id)


def login_test_client(client, user_id, *, fresh=True):
    """Store Flask-Login session keys for a test client."""
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = bool(fresh)


def authenticated_test_client(app, user, *, fresh=True):
    """Create a Flask test client logged in as a persisted user.

    Args:
        app: Flask application under test.
        user: Persisted user mapping containing at least an ``id`` value.
        fresh: Flask-Login freshness flag to store in the session.

    Returns:
        A Flask test client with ``test_user`` attached for assertions.
    """
    test_client = app.test_client()
    login_test_client(test_client, user["id"], fresh=fresh)
    test_client.test_user = dict(user)
    return test_client


@pytest.fixture(autouse=True)
def block_network_calls(monkeypatch):
    """Prevent tests from opening real network connections."""
    install_network_guard(monkeypatch)


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Create and configure a Flask application for tests."""
    test_settings = replace(
        config_module.settings,
        database_path=tmp_path / "finance-test.db",
        database_url=config_module.sqlite_database_url(tmp_path / "finance-test.db"),
        secret_key="test-secret",
    )
    monkeypatch.setattr(config_module, "settings", test_settings)
    monkeypatch.setattr(engine_module, "settings", test_settings)
    monkeypatch.setattr(app_package, "settings", test_settings)
    engine_module.dispose_database_engine()

    initialize_test_database(test_settings.database_path)

    application = app_package.create_app()
    application.config["TESTING"] = True
    application.config["TEST_DATABASE_PATH"] = test_settings.database_path
    seed_test_owner()
    yield application
    engine_module.dispose_database_engine()


@pytest.fixture
def owner_client(app):
    """A test client authenticated as the default owner."""
    with engine_module.db_core_transaction() as conn:
        owner = auth_repository.get_user_by_username(conn, TEST_OWNER_USERNAME)
    return authenticated_test_client(app, owner)


@pytest.fixture
def client(owner_client):
    """A backwards-compatible owner client fixture for existing tests."""
    return owner_client


@pytest.fixture
def editor_client(app):
    """A test client authenticated as an editor user."""
    editor = get_or_create_test_user(TEST_EDITOR_USERNAME, USER_ROLE_EDITOR)
    return authenticated_test_client(app, editor)


@pytest.fixture
def viewer_client(app):
    """A test client authenticated as a viewer user."""
    viewer = get_or_create_test_user(TEST_VIEWER_USERNAME, USER_ROLE_VIEWER)
    return authenticated_test_client(app, viewer)


@pytest.fixture
def stale_session_client(app):
    """A test client authenticated with a non-fresh owner session."""
    with engine_module.db_core_transaction() as conn:
        owner = auth_repository.get_user_by_username(conn, TEST_OWNER_USERNAME)
    return authenticated_test_client(app, owner, fresh=False)


@pytest.fixture
def must_change_password_client(app):
    """A test client authenticated as a user forced to change password."""
    user = get_or_create_test_user(
        TEST_MUST_CHANGE_USERNAME,
        USER_ROLE_VIEWER,
        must_change_password=True,
    )
    return authenticated_test_client(app, user)


@pytest.fixture
def csrf_client(client):
    """A test client authenticated as the default owner with CSRF helpers."""
    return CsrfEnabledClient(client)


@pytest.fixture
def anonymous_client(app):
    """A test client without a logged-in user."""
    return app.test_client()


@pytest.fixture
def anonymous_csrf_client(anonymous_client):
    """An anonymous test client with CSRF helpers."""
    return CsrfEnabledClient(anonymous_client)


@pytest.fixture
def runner(app):
    """A CLI runner for the Flask application."""
    return app.test_cli_runner()


@pytest.fixture
def core_conn(app):
    """Open a Core transaction against the test application's database."""
    with engine_module.db_core_transaction() as conn:
        yield conn


@pytest.fixture
def data_factory(core_conn):
    """Create test data through a raw SQLAlchemy Core connection."""
    return TestDataFactory(core_conn)
