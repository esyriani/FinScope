"""Template value formatting helpers.

Provides shared Jinja filters for dates, timestamps, and money values.
Timestamp presentation uses the configured application timezone.
"""

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from finance_app.core import config as config_module
from finance_app.core.i18n import current_language, month_abbreviation, normalize_language
from finance_app.core.money import MoneyValue, format_money_display, format_number_display
from finance_app.database.dates import coerce_utc_datetime

LOCAL_TIMEZONE_FALLBACKS = {"local", "system"}


def format_date(value: object, language: object | None = None) -> str:
    """Format an ISO date value for template display."""
    if not value:
        return ""

    date_obj = coerce_date(value)
    active_language = normalize_language(language or current_language())
    month = month_abbreviation(date_obj.month, active_language)
    if active_language == "fr":
        return f"{date_obj.day} {month} {date_obj.year}"
    return f"{month} {date_obj.day}, {date_obj.year}"


def coerce_date(value: object) -> date:
    """Return a date object from a date, datetime, or ISO date string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def configured_timezone_name(timezone_name: object | None = None) -> str:
    """Return the requested or configured timezone name.

    Args:
        timezone_name: Optional IANA timezone name. When omitted, the value
            from application settings is used.

    Returns:
        A stripped timezone name, defaulting to ``UTC`` when blank.
    """
    return str(timezone_name or config_module.settings.timezone or "UTC").strip() or "UTC"


def localize_utc_datetime(value: datetime, timezone_name: object | None = None) -> datetime:
    """Convert a naive UTC datetime into the configured display timezone.

    Args:
        value: A naive ``datetime`` that represents UTC.
        timezone_name: Optional IANA timezone override.

    Returns:
        A timezone-aware ``datetime`` converted for display. Invalid timezone
        names fall back to UTC. IANA data comes from the system or the required
        ``tzdata`` package on Windows.
    """
    utc_value = value.replace(tzinfo=timezone.utc)
    name = configured_timezone_name(timezone_name)
    if name.upper() == "UTC":
        return utc_value

    try:
        return utc_value.astimezone(ZoneInfo(name))
    except ZoneInfoNotFoundError:
        if name in LOCAL_TIMEZONE_FALLBACKS:
            return utc_value.astimezone()
        return utc_value


def format_datetime(
    value: object,
    timezone_name: object | None = None,
    language: object | None = None,
) -> str:
    """Format a UTC date-time value in the configured display timezone.

    Args:
        value: A UTC ISO timestamp, ``datetime``, or ``date`` value.
        timezone_name: Optional IANA timezone override, mainly for tests.

    Returns:
        A timestamp string formatted for the active UI language.
    """
    if not value:
        return ""

    date_obj = coerce_utc_datetime(value)
    assert date_obj is not None
    display_datetime = localize_utc_datetime(date_obj, timezone_name)
    return f"{format_date(display_datetime.date(), language=language)} {display_datetime:%H:%M:%S}"


def format_money(value: MoneyValue | None) -> str:
    """Format a money value for template display."""
    return format_money_display(value)


def format_number(value: MoneyValue | None, places: int = 2, signed: bool = False) -> str:
    """Format a number for template display."""
    return format_number_display(value, places=places, signed=signed)


def format_percent(value: MoneyValue | None, places: int = 1, signed: bool = False) -> str:
    """Format a percentage for template display."""
    formatted = format_number_display(value, places=places, signed=signed)
    if not formatted:
        return ""
    return f"{formatted} %" if normalize_language(current_language()) == "fr" else f"{formatted}%"


def register_filters(app: Any) -> None:
    """Register shared Jinja value formatting filters."""
    app.jinja_env.filters["datefmt"] = format_date
    app.jinja_env.filters["datetimefmt"] = format_datetime
    app.jinja_env.filters["moneyfmt"] = format_money
    app.jinja_env.filters["numberfmt"] = format_number
    app.jinja_env.filters["percentfmt"] = format_percent
