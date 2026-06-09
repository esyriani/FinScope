"""Parsing helpers for the comparison feature."""

from collections.abc import Iterable

from finance_app.modules.comparison.constants import PERIOD_COMPARISON_OPTIONS

COMPARISON_VIEW_OPTIONS = {"period", "year"}


def parse_selected_years(values: Iterable[object]) -> list[int]:
    """Parse selected years."""
    years: list[int] = []
    for value in values:
        try:
            year = int(str(value))
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)

    return years


def clean_categories(values: Iterable[str]) -> list[str]:
    """Clean categories."""
    return clean_filter_values(values)


def clean_tags(values: Iterable[str]) -> list[str]:
    """Clean tag names."""
    return clean_filter_values(values)


def clean_filter_values(values: Iterable[str]) -> list[str]:
    """Clean repeated string filter values."""
    return [value.strip() for value in values if value.strip()]


def parse_period_comparison(value: object) -> str:
    """Parse period comparison."""
    value = str(value or "").strip()
    return value if value in PERIOD_COMPARISON_OPTIONS else "month_previous"


def parse_comparison_view(value: object) -> str:
    """Parse the active comparison page tab."""
    value = str(value or "").strip()
    return value if value in COMPARISON_VIEW_OPTIONS else "period"


def parse_baseline_year(value: object, selected_years: Iterable[int]) -> int | None:
    """Parse baseline year."""
    try:
        baseline_year = int(str(value))
    except (TypeError, ValueError):
        return None
    return baseline_year if baseline_year in selected_years else None
