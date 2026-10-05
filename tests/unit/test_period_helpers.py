"""Unit tests for shared date-period helpers."""

from datetime import date, timedelta

import pytest

from finance_app.core.periods import (
    DEFAULT_DATE_PERIOD,
    PERIOD_6_MONTHS,
    PERIOD_90_DAYS,
    PERIOD_ALL,
    PERIOD_CUSTOM,
    PERIOD_MONTH,
    PERIOD_WEEK,
    PERIOD_YEAR,
    PERIOD_YEAR_TO_DATE,
    format_date_label,
    get_period_label,
    normalize_date_period,
    parse_iso_date,
    period_start_date,
    previous_period_date_range,
    shift_months,
    shift_years,
)

TODAY = date(2026, 7, 15)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (PERIOD_WEEK, PERIOD_WEEK),
        (PERIOD_ALL, PERIOD_ALL),
        (PERIOD_CUSTOM, PERIOD_CUSTOM),
        ("unsupported", DEFAULT_DATE_PERIOD),
        ("", DEFAULT_DATE_PERIOD),
        (None, DEFAULT_DATE_PERIOD),
    ],
)
def test_normalize_date_period_defaults_unsupported_values(value, expected):
    """Verify only supported reporting period identifiers are accepted."""
    assert normalize_date_period(value) == expected


@pytest.mark.parametrize(
    ("value", "months", "expected"),
    [
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2025, 3, 31), -1, date(2025, 2, 28)),
        (date(2025, 12, 31), 2, date(2026, 2, 28)),
    ],
)
def test_shift_months_clamps_end_of_month_across_boundaries(value, months, expected):
    """Verify month shifts keep the closest valid day in the target month."""
    assert shift_months(value, months) == expected


@pytest.mark.parametrize(
    ("value", "years", "expected"),
    [
        (date(2024, 2, 29), 1, date(2025, 2, 28)),
        (date(2024, 2, 29), -4, date(2020, 2, 29)),
        (date(2026, 9, 16), -1, date(2025, 9, 16)),
    ],
)
def test_shift_years_clamps_leap_day(value, years, expected):
    """Verify year shifts preserve dates unless the target year has no leap day."""
    assert shift_years(value, years) == expected


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        (PERIOD_WEEK, TODAY - timedelta(days=7)),
        (PERIOD_MONTH, date(2026, 6, 15)),
        (PERIOD_90_DAYS, TODAY - timedelta(days=90)),
        (PERIOD_6_MONTHS, date(2026, 1, 15)),
        (PERIOD_YEAR_TO_DATE, date(2026, 1, 1)),
        (PERIOD_YEAR, date(2025, 7, 15)),
        (PERIOD_ALL, None),
        (PERIOD_CUSTOM, None),
    ],
)
def test_period_start_date_resolves_supported_periods(period, expected):
    """Verify reporting periods map to inclusive start dates."""
    assert period_start_date(period, TODAY) == expected


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        (PERIOD_WEEK, (TODAY - timedelta(days=14), TODAY - timedelta(days=7))),
        (PERIOD_MONTH, (date(2026, 5, 15), date(2026, 6, 15))),
        (PERIOD_90_DAYS, (TODAY - timedelta(days=180), TODAY - timedelta(days=90))),
        (PERIOD_6_MONTHS, (date(2025, 7, 15), date(2026, 1, 15))),
        (PERIOD_YEAR_TO_DATE, (date(2025, 1, 1), date(2025, 7, 16))),
        (PERIOD_YEAR, (date(2024, 7, 15), date(2025, 7, 15))),
        (PERIOD_ALL, (None, None)),
        (PERIOD_CUSTOM, (None, None)),
    ],
)
def test_previous_period_date_range_resolves_supported_periods(period, expected):
    """Verify previous period windows use inclusive start and exclusive end dates."""
    assert previous_period_date_range(period, TODAY) == expected


def test_invalid_periods_use_default_year_to_date_window():
    """Verify invalid period identifiers are normalized before date-window resolution."""
    assert period_start_date("bad", TODAY) == date(2026, 1, 1)
    assert previous_period_date_range("bad", TODAY) == (date(2025, 1, 1), date(2025, 7, 16))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-16", "2026-09-16"),
        (" 2026-09-16 ", "2026-09-16"),
        ("", ""),
        (None, ""),
        ("09/16/2026", ""),
        ("2026-02-30", ""),
    ],
)
def test_parse_iso_date_accepts_only_canonical_dates(value, expected):
    """Verify invalid or blank date values collapse to an empty string."""
    assert parse_iso_date(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-16", "16-Sep-2026"),
        ("2026-02-30", ""),
        ("", ""),
    ],
)
def test_format_date_label_uses_stable_short_month_labels(value, expected):
    """Verify date labels use the compact page-label shape."""
    assert format_date_label(value) == expected


@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        ("2026-01-01", "2026-01-31", "01-Jan-2026 to 31-Jan-2026"),
        ("2026-01-01", "", "from 01-Jan-2026"),
        ("", "2026-01-31", "through 31-Jan-2026"),
        ("", "", "custom range"),
    ],
)
def test_get_period_label_formats_custom_range_branches(date_from, date_to, expected):
    """Verify custom labels describe whichever range boundary is present."""
    assert get_period_label(PERIOD_CUSTOM, date_from, date_to) == expected


def test_get_period_label_formats_named_periods_and_defaults_invalid_values():
    """Verify non-custom labels use the supported period display names."""
    assert get_period_label(PERIOD_MONTH) == "Last month"
    assert get_period_label("bad") == "Year to date"
