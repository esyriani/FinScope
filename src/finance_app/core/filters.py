"""Filter parsing helpers for the filters feature."""

from datetime import datetime

from finance_app.core.config import settings
from finance_app.core.i18n import month_abbreviation


def format_date(value):
    """Format date."""
    if not value:
        return ""

    date_obj = datetime.strptime(value, "%Y-%m-%d")
    return f"{date_obj.day:02d}-{month_abbreviation(date_obj.month)}-{date_obj.year}"


def format_money(value):
    """Format money."""
    if value is None:
        return ""

    return f"{value:,.2f}".replace(",", " ") + f" $"


def register_filters(app):
    """Register filters."""
    app.jinja_env.filters["datefmt"] = format_date
    app.jinja_env.filters["moneyfmt"] = format_money
