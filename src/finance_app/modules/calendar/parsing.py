"""Parsing helpers for the calendar feature."""

from collections.abc import Iterable
from datetime import date, datetime

from .constants import HEATMAP_OPTIONS


def parse_month(value: object) -> date | None:
    """Parse month."""
    try:
        return datetime.strptime(str(value or ""), "%Y-%m").date()
    except ValueError:
        return None


def parse_heatmap_metric(value: object) -> str:
    """Parse heatmap metric."""
    value = str(value or "").strip()
    return value if value in HEATMAP_OPTIONS else "spending"


def clean_categories(values: Iterable[str]) -> list[str]:
    """Clean categories."""
    return clean_filter_values(values)


def clean_tags(values: Iterable[str]) -> list[str]:
    """Clean tag names."""
    return clean_filter_values(values)


def clean_filter_values(values: Iterable[str]) -> list[str]:
    """Clean repeated string filter values."""
    return [value.strip() for value in values if value.strip()]


def default_month() -> date:
    """Handle default month."""
    today = date.today()
    return today.replace(day=1)


def shift_month(value: date, offset: int) -> date:
    """Shift month."""
    month_index = value.month - 1 + offset
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def month_number(month_key: str) -> int:
    """Return number."""
    year, month = month_key.split("-", 1)
    return (int(year) * 12) + int(month)
