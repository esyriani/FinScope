"""Filter parsing helpers for the filters feature."""

from datetime import datetime

from finance_app.core.config import settings
from finance_app.core.i18n import month_abbreviation
from finance_app.database.dates import coerce_utc_datetime


def format_date(value):
    """Format date."""
    if not value:
        return ""

    date_obj = datetime.strptime(value, "%Y-%m-%d")
    return f"{date_obj.day:02d}-{month_abbreviation(date_obj.month)}-{date_obj.year}"


def format_datetime(value):
    """Format a date-time value with the app-wide timestamp presentation."""
    if not value:
        return ""

    date_obj = coerce_utc_datetime(value)
    return (
        f"{date_obj.day:02d}-{month_abbreviation(date_obj.month)}-{date_obj.year} "
        f"{date_obj.hour:02d}:{date_obj.minute:02d}:{date_obj.second:02d}"
    )


def format_money(value):
    """Format money."""
    if value is None:
        return ""

    return f"{value:,.2f}".replace(",", " ") + f" $"


def register_filters(app):
    """Register filters."""
    app.jinja_env.filters["datefmt"] = format_date
    app.jinja_env.filters["datetimefmt"] = format_datetime
    app.jinja_env.filters["moneyfmt"] = format_money
