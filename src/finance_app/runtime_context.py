"""Shared request runtime context for template globals.

This module is the application-level boundary between Flask request hooks,
runtime setting persistence, and static built-in taxonomy metadata. It loads
request-scoped UI settings once per request and exposes template-safe values
without performing feature repository work during template rendering.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from flask import g, has_request_context, request

from finance_app.core.builtin_taxonomy import builtin_category_names
from finance_app.core.config import settings
from finance_app.core.constants import THEME_MODE_DARK, THEME_MODE_LIGHT
from finance_app.core.i18n import locale_for_language, normalize_language
from finance_app.database.engine import db_core_connection
from finance_app.modules.categories.repository import (
    CATEGORY_DATABASE_UNAVAILABLE_ERRORS,
    fetch_builtin_category_names,
    is_missing_categories_table_error,
)
from finance_app.modules.settings.runtime import DATABASE_OPERATIONAL_ERRORS, get_all_settings

_RUNTIME_CONTEXT_ATTR = "finance_runtime_context"


@dataclass(frozen=True)
class RuntimeTemplateContext:
    """Request-scoped values shared by all rendered templates."""

    ui_theme: str
    ui_language: str
    ui_locale: str
    category_filter_builtin_exclusions: tuple[str, ...]


def load_request_runtime_context() -> RuntimeTemplateContext:
    """Load and cache shared runtime context for the active request."""
    context = (
        default_runtime_template_context()
        if request.endpoint == "static"
        else load_persisted_runtime_template_context()
    )
    setattr(g, _RUNTIME_CONTEXT_ATTR, context)
    g.ui_language = context.ui_language
    return context


def current_runtime_template_context() -> RuntimeTemplateContext:
    """Return cached template context without opening database connections."""
    if has_request_context():
        context = getattr(g, _RUNTIME_CONTEXT_ATTR, None)
        if isinstance(context, RuntimeTemplateContext):
            return context
    return default_runtime_template_context()


def load_persisted_runtime_template_context() -> RuntimeTemplateContext:
    """Load database-backed runtime values once for the active request."""
    with db_core_connection() as conn:
        runtime_settings = load_runtime_ui_settings(conn)
        category_exclusions = load_builtin_category_exclusions(conn)
    return build_runtime_template_context(runtime_settings, category_exclusions=category_exclusions)


def load_runtime_ui_settings(conn: object) -> Mapping[str, str]:
    """Return persisted runtime UI settings from an active connection."""
    try:
        return get_all_settings(conn)
    except DATABASE_OPERATIONAL_ERRORS:
        return {}


def load_builtin_category_exclusions(conn: object) -> tuple[str, ...]:
    """Return persisted built-in category names without seeding taxonomy rows."""
    try:
        categories = fetch_builtin_category_names(conn)
    except CATEGORY_DATABASE_UNAVAILABLE_ERRORS as exc:
        if not is_missing_categories_table_error(exc):
            raise
        return builtin_category_names()
    return tuple(categories) or builtin_category_names()


def build_runtime_template_context(
    runtime_settings: Mapping[str, str],
    *,
    category_exclusions: tuple[str, ...] | None = None,
) -> RuntimeTemplateContext:
    """Build template globals from already-loaded runtime settings."""
    ui_language = normalize_language(runtime_settings.get("ui_language", settings.locale))
    theme_mode = normalize_theme_mode(runtime_settings.get("theme_mode", THEME_MODE_DARK))
    return RuntimeTemplateContext(
        ui_theme=theme_mode,
        ui_language=ui_language,
        ui_locale=locale_for_language(ui_language),
        category_filter_builtin_exclusions=category_exclusions or builtin_category_names(),
    )


def default_runtime_template_context() -> RuntimeTemplateContext:
    """Return template globals that do not require database access."""
    return build_runtime_template_context(
        {
            "theme_mode": THEME_MODE_DARK,
            "ui_language": settings.locale,
        }
    )


def normalize_theme_mode(value: object) -> str:
    """Return a supported Bootstrap theme value."""
    return THEME_MODE_DARK if str(value).strip().lower() == THEME_MODE_DARK else THEME_MODE_LIGHT
