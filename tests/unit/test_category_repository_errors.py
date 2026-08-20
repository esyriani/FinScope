"""Tests for category repository error handling.

Verifies taxonomy option fallbacks are limited to expected pre-initialization
database states and do not hide schema or programming defects.
"""

import pytest
from sqlalchemy.exc import OperationalError

from finance_app.core.builtin_taxonomy import builtin_category_names
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.modules.categories import repository


def operational_error(message):
    """Build a representative SQLAlchemy operational error for tests."""
    return OperationalError("SELECT categories.name", {}, Exception(message))


def raising_fetch(exc):
    """Return a fetch helper that raises the supplied exception."""

    def _raise(_conn):
        raise exc

    return _raise


def test_category_options_fall_back_only_when_categories_table_is_missing(monkeypatch):
    """Verify UNKNOWN fallback remains available before taxonomy tables exist."""
    monkeypatch.setattr(
        repository,
        "fetch_category_names",
        raising_fetch(operational_error("no such table: categories")),
    )

    assert repository.get_category_options(object()) == [UNKNOWN_CATEGORY]


def test_builtin_category_names_fall_back_only_when_categories_table_is_missing(monkeypatch):
    """Verify built-in fallback remains available before taxonomy tables exist."""
    monkeypatch.setattr(
        repository,
        "fetch_builtin_category_names",
        raising_fetch(operational_error("no such table: categories")),
    )

    assert repository.get_builtin_category_names(object()) == list(builtin_category_names())


def test_category_options_raise_schema_errors(monkeypatch):
    """Verify broken schemas are not hidden behind UNKNOWN fallback."""
    error = operational_error("no such column: categories.builtin_key")
    monkeypatch.setattr(repository, "fetch_category_names", raising_fetch(error))

    with pytest.raises(OperationalError, match="builtin_key"):
        repository.get_category_options(object())


def test_builtin_category_names_raise_programming_errors(monkeypatch):
    """Verify non-database defects are not hidden behind built-in fallback."""
    monkeypatch.setattr(
        repository,
        "fetch_builtin_category_names",
        raising_fetch(RuntimeError("query builder bug")),
    )

    with pytest.raises(RuntimeError, match="query builder bug"):
        repository.get_builtin_category_names(object())
