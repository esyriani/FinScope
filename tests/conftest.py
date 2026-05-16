"""Shared pytest fixtures for application and database tests."""

import sys
import shutil
from dataclasses import replace
from pathlib import Path

sys.dont_write_bytecode = True

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finance_app as app_package  # noqa: E402
from finance_app.core import config as config_module  # noqa: E402
from finance_app.database import engine as engine_module  # noqa: E402
from finance_app.database.seeds import (  # noqa: E402
    seed_category_taxonomy_defaults,
    seed_runtime_settings_defaults,
    seed_statement_type_defaults,
)
from finance_app.database.tables import metadata  # noqa: E402


LAYER_MARKERS = {
    "unit": "unit",
    "integration": "integration",
    "routes": "route",
    "smoke": "smoke",
}
DB_FIXTURES = {"db_conn", "core_conn"}
FLASK_FIXTURES = {"app", "client", "runner"}


class TestRow:
    """Expose SQLAlchemy result rows with mapping and tuple-style access for tests."""

    __test__ = False

    def __init__(self, row):
        """Store the wrapped SQLAlchemy row."""
        self._row = row
        self._mapping = row._mapping

    def __getitem__(self, key):
        """Return row values by column name, index, or slice."""
        if isinstance(key, str):
            return self._mapping[key]
        return self._row[key]

    def __iter__(self):
        """Iterate row values."""
        return iter(self._row)

    def __len__(self):
        """Return the number of columns in the row."""
        return len(self._row)

    def keys(self):
        """Return available column names."""
        return self._mapping.keys()

    def get(self, key, default=None):
        """Return a named value or a default."""
        return self._mapping.get(key, default)


class TestResult:
    """Wrap SQLAlchemy results returned by SQLAlchemy test queries."""

    __test__ = False

    def __init__(self, result):
        """Store the wrapped SQLAlchemy result."""
        self._result = result

    @property
    def rowcount(self):
        """Return the number of rows affected by the statement."""
        return self._result.rowcount

    @property
    def lastrowid(self):
        """Return the inserted row id when available."""
        return self._result.lastrowid

    def fetchone(self):
        """Return one row with mapping-style keyed access."""
        row = self._result.fetchone()
        return None if row is None else TestRow(row)

    def fetchall(self):
        """Return all rows with mapping-style keyed access."""
        return [TestRow(row) for row in self._result.fetchall()]

    def __iter__(self):
        """Iterate rows with mapping-style keyed access."""
        for row in self._result:
            yield TestRow(row)

    def scalar_one(self):
        """Return exactly one scalar value."""
        return self._result.scalar_one()


class TestDatabaseConnection:
    """SQLAlchemy-backed connection adapter for integration tests.

    Runtime helpers receive the underlying Core connection semantics, while
    string-based setup and assertions in tests use SQLAlchemy text constructs.
    """

    __test__ = False

    def __init__(self, conn):
        """Store the wrapped SQLAlchemy Core connection."""
        self._conn = conn

    def execute(self, statement, parameters=None):
        """Execute Core expressions or SQLAlchemy text statements for tests."""
        if isinstance(statement, str):
            statement, parameters = build_text_statement(statement, parameters)
            result = self._conn.execute(statement, parameters)
            return TestResult(result)
        if parameters is None:
            return self._conn.execute(statement)
        return self._conn.execute(statement, parameters)

    def executemany(self, statement, seq_of_parameters):
        """Execute a SQLAlchemy text statement once for each parameter set."""
        statement, parameters = build_many_text_statement(statement, seq_of_parameters)
        return TestResult(self._conn.execute(statement, parameters))

    def commit(self):
        """Commit the current transaction."""
        self._conn.commit()

    def rollback(self):
        """Roll back the current transaction."""
        self._conn.rollback()

    def begin_nested(self):
        """Begin a nested transaction on the wrapped Core connection."""
        return self._conn.begin_nested()


def build_text_statement(statement, parameters=None):
    """Return a SQLAlchemy text statement and named bind parameters."""
    if parameters is None:
        return text(statement), {}
    if isinstance(parameters, dict):
        return text(statement), parameters

    return bind_positional_text_statement(statement, parameters)


def build_many_text_statement(statement, seq_of_parameters):
    """Return a SQLAlchemy text statement and row bind mappings."""
    rows = list(seq_of_parameters)
    if not rows:
        return text(statement), []
    if isinstance(rows[0], dict):
        return text(statement), rows

    statement, _ = bind_positional_text_statement(statement, rows[0])
    names = positional_bind_names(rows[0])
    return statement, [
        dict(zip(names, row))
        for row in rows
    ]


def bind_positional_text_statement(statement, parameters):
    """Convert positional question-mark binds into SQLAlchemy named binds."""
    values = tuple(parameters)
    names = positional_bind_names(values)
    parts = statement.split("?")
    placeholder_count = len(parts) - 1
    if placeholder_count != len(values):
        raise ValueError(
            f"Expected {placeholder_count} parameters for SQL statement, got {len(values)}"
        )

    converted = []
    bind_parameters = {}
    for index, part in enumerate(parts[:-1]):
        name = names[index]
        converted.append(part)
        converted.append(f":{name}")
        bind_parameters[name] = values[index]
    converted.append(parts[-1])
    return text("".join(converted)), bind_parameters


def positional_bind_names(parameters):
    """Return deterministic bind names for a positional parameter row."""
    return [f"p{index}" for index, _ in enumerate(parameters)]


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
    """Create the full application schema in a temporary SQLite database."""
    del database_path
    engine = engine_module.get_database_engine()
    metadata.create_all(engine)
    with engine.begin() as conn:
        seed_runtime_settings_defaults(conn)
        seed_statement_type_defaults(conn)
        seed_category_taxonomy_defaults(conn)


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
    yield application
    engine_module.dispose_database_engine()


@pytest.fixture
def client(app):
    """A test client for the Flask application."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """A CLI runner for the Flask application."""
    return app.test_cli_runner()


@pytest.fixture
def db_conn(app):
    """Open a SQLAlchemy-backed test connection to the app database."""
    with engine_module.db_core_transaction() as conn:
        yield TestDatabaseConnection(conn)


@pytest.fixture
def core_conn(app):
    """Open a Core transaction against the test application's database."""
    with engine_module.db_core_transaction() as conn:
        yield conn
