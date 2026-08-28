"""Parsing helpers for recurring activity filters and month navigation."""

from collections.abc import Iterable
from datetime import date, datetime


def parse_month(value: object) -> date | None:
    """Parse a year-month query value into the first day of that month."""
    try:
        return datetime.strptime(str(value or ""), "%Y-%m").date()
    except ValueError:
        return None


def clean_categories(values: Iterable[str]) -> list[str]:
    """Clean repeated category filter values."""
    return clean_filter_values(values)


def clean_tags(values: Iterable[str]) -> list[str]:
    """Clean repeated tag filter values."""
    return clean_filter_values(values)


def clean_filter_values(values: Iterable[str]) -> list[str]:
    """Clean repeated string filter values."""
    return [value.strip() for value in values if value.strip()]


def default_month() -> date:
    """Return the first day of the current local month."""
    today = date.today()
    return today.replace(day=1)


def shift_month(value: date, offset: int) -> date:
    """Return the first day of a month shifted by the given month offset."""
    month_index = value.month - 1 + offset
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def month_number(month_key: str) -> int:
    """Return a comparable integer for a YYYY-MM month key."""
    year, month = month_key.split("-", 1)
    return (int(year) * 12) + int(month)
