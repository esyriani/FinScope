"""User interface translation helpers.

Loads source-string translation catalogs from JSON files and exposes small
helpers for Flask templates, Python routes, and browser-side scripts.
English source text is the canonical message id.
"""

from functools import lru_cache
import json
from pathlib import Path
from string import Formatter

from flask import g, has_request_context

from finance_app.core.constants import BASE_DIR


DEFAULT_LANGUAGE = "en"
DEFAULT_LOCALE = "en-CA"
SUPPORTED_LANGUAGES = {
    "en": "English",
    "fr": "French",
}
LANGUAGE_LOCALES = {
    "en": "en-CA",
    "fr": "fr-CA",
}
TRANSLATIONS_DIR = Path(BASE_DIR) / "translations"
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTH_ABBREVIATIONS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
WEEKDAY_ABBREVIATIONS = (
    "Mon",
    "Tue",
    "Wed",
    "Thu",
    "Fri",
    "Sat",
    "Sun",
)


def normalize_language(value):
    """Return a supported language code from a user or locale value.

    Args:
        value: A language or locale string such as ``en``, ``en_CA``, or ``fr-CA``.

    Returns:
        ``en`` or ``fr``. Unsupported or blank values fall back to English.
    """
    text = str(value or "").strip().lower().replace("-", "_")
    language = text.split("_", 1)[0]
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def locale_for_language(language):
    """Return the browser locale associated with a supported language."""
    return LANGUAGE_LOCALES.get(normalize_language(language), DEFAULT_LOCALE)


def current_language():
    """Return the language selected for the current request, if any."""
    if has_request_context():
        return normalize_language(getattr(g, "ui_language", DEFAULT_LANGUAGE))
    return DEFAULT_LANGUAGE


def gettext(message, **variables):
    """Translate a message for the current request language."""
    return translate(message, current_language(), **variables)


def month_name(month, language=None):
    """Return a localized full month name for a one-based month number."""
    index = int(month) - 1
    if index < 0 or index >= len(MONTH_NAMES):
        return ""
    return translate(MONTH_NAMES[index], language or current_language())


def month_abbreviation(month, language=None):
    """Return a localized short month label for a one-based month number."""
    index = int(month) - 1
    if index < 0 or index >= len(MONTH_ABBREVIATIONS):
        return ""
    return translate(MONTH_ABBREVIATIONS[index], language or current_language())


def month_abbreviation_labels(language=None):
    """Return localized short month labels for January through December."""
    active_language = language or current_language()
    return [
        translate(month, active_language)
        for month in MONTH_ABBREVIATIONS
    ]


def weekday_abbreviation_labels(language=None):
    """Return localized weekday labels starting on Monday."""
    active_language = language or current_language()
    return [
        translate(day, active_language)
        for day in WEEKDAY_ABBREVIATIONS
    ]


def format_month_year(value, language=None):
    """Return a localized month and year label for a ``date`` value."""
    return f"{month_name(value.month, language)} {value.year}".strip()


def client_translations(language, messages):
    """Return translated strings for JavaScript-visible messages.

    Args:
        language: Requested language code.
        messages: Iterable of source English messages needed by client scripts.

    Returns:
        A mapping from each source message to its translated display string.
    """
    return {
        str(message): translate(str(message), language)
        for message in messages
    }


def translate(message, language=None, **variables):
    """Translate and optionally format a source English message.

    Args:
        message: Source English text used as the catalog key.
        language: Optional target language. The current default is English.
        **variables: Format values used with ``str.format`` placeholders.

    Returns:
        A translated string, falling back to the source message when a catalog
        entry is unavailable or malformed.
    """
    source = str(message)
    catalog = _load_catalog(normalize_language(language))
    template = catalog.get(source, source)
    return _format_message(template, source, variables)


@lru_cache(maxsize=None)
def _load_catalog(language):
    """Load a translation catalog from disk."""
    if normalize_language(language) == DEFAULT_LANGUAGE:
        return {}

    catalog_path = TRANSLATIONS_DIR / f"{normalize_language(language)}.json"
    try:
        with catalog_path.open("r", encoding="utf-8") as handle:
            raw_catalog = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw_catalog, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in raw_catalog.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _format_message(template, source, variables):
    """Safely format a translated string with named variables."""
    if not variables:
        return template

    try:
        _validate_format_variables(template, variables)
        return template.format(**variables)
    except (KeyError, ValueError):
        return source.format(**variables)


def _validate_format_variables(template, variables):
    """Reject translated placeholders that were not supplied by the caller."""
    field_names = {
        field_name
        for _, field_name, _, _ in Formatter().parse(template)
        if field_name
    }
    missing = field_names - set(variables)
    if missing:
        raise KeyError(next(iter(missing)))
