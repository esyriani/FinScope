"""Parsing helpers for the calendar feature."""

from datetime import date, datetime

from .constants import HEATMAP_OPTIONS


def parse_month(value):
    """Parse month."""
    try:
        return datetime.strptime(str(value or ""), "%Y-%m").date()
    except ValueError:
        return None


def parse_heatmap_metric(value):
    """Parse heatmap metric."""
    value = str(value or "").strip()
    return value if value in HEATMAP_OPTIONS else "spending"


def clean_categories(values):
    """Clean categories."""
    return clean_filter_values(values)


def clean_tags(values):
    """Clean tag names."""
    return clean_filter_values(values)


def clean_filter_values(values):
    """Clean repeated string filter values."""
    return [value.strip() for value in values if value.strip()]


def default_month():
    """Handle default month."""
    today = date.today()
    return today.replace(day=1)


def shift_month(value, offset):
    """Shift month."""
    month_index = value.month - 1 + offset
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def month_number(month_key):
    """Return number."""
    year, month = month_key.split("-", 1)
    return (int(year) * 12) + int(month)
