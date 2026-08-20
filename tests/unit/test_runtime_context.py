"""Tests for shared request runtime template context."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from finance_app import runtime_context
from finance_app.core.builtin_taxonomy import builtin_category_names
from finance_app.core.constants import THEME_MODE_DARK, THEME_MODE_LIGHT


def test_load_request_runtime_context_caches_ui_settings_once(app, monkeypatch):
    """Verify request template context does not reload settings during rendering."""
    fake_connection = object()
    calls = 0

    @contextmanager
    def fake_db_core_connection() -> Iterator[object]:
        yield fake_connection

    def fake_get_all_settings(conn: Any) -> dict[str, str]:
        nonlocal calls
        assert conn is fake_connection
        calls += 1
        return {"ui_language": "fr-CA", "theme_mode": "dark"}

    def fake_fetch_builtin_category_names(conn: Any) -> list[str]:
        assert conn is fake_connection
        return ["System adjustment", *builtin_category_names()]

    monkeypatch.setattr(runtime_context, "db_core_connection", fake_db_core_connection)
    monkeypatch.setattr(runtime_context, "get_all_settings", fake_get_all_settings)
    monkeypatch.setattr(runtime_context, "fetch_builtin_category_names", fake_fetch_builtin_category_names)

    with app.test_request_context("/reports"):
        loaded = runtime_context.load_request_runtime_context()
        first_render_context = runtime_context.current_runtime_template_context()
        second_render_context = runtime_context.current_runtime_template_context()

    assert calls == 1
    assert first_render_context is loaded
    assert second_render_context is loaded
    assert loaded.ui_theme == THEME_MODE_DARK
    assert loaded.ui_language == "fr"
    assert loaded.ui_locale == "fr-CA"
    assert loaded.category_filter_builtin_exclusions == ("System adjustment", *builtin_category_names())


def test_runtime_template_context_uses_safe_defaults_without_loaded_request_context(app):
    """Verify render-time fallback context does not require database access."""
    with app.test_request_context("/reports"):
        context = runtime_context.current_runtime_template_context()

    assert context.ui_theme in {THEME_MODE_DARK, THEME_MODE_LIGHT}
    assert context.category_filter_builtin_exclusions == builtin_category_names()
