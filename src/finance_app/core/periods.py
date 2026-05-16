"""Date-period parsing and labeling helpers."""

from calendar import monthrange
from datetime import date, datetime, timedelta

from finance_app.core.i18n import gettext, month_abbreviation

PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
PERIOD_90_DAYS = "90d"
PERIOD_6_MONTHS = "6m"
PERIOD_YEAR_TO_DATE = "ytd"
PERIOD_YEAR = "year"
PERIOD_ALL = "all"
PERIOD_CUSTOM = "custom"
DATE_PERIODS = (
    PERIOD_WEEK,
    PERIOD_MONTH,
    PERIOD_90_DAYS,
    PERIOD_6_MONTHS,
    PERIOD_YEAR_TO_DATE,
    PERIOD_YEAR,
    PERIOD_ALL,
    PERIOD_CUSTOM,
)
DEFAULT_DATE_PERIOD = PERIOD_YEAR_TO_DATE
DATE_PERIOD_OPTIONS = (
    (PERIOD_WEEK, "Last 7 days"),
    (PERIOD_MONTH, "Last month"),
    (PERIOD_90_DAYS, "Last 90 days"),
    (PERIOD_6_MONTHS, "Last 6 months"),
    (PERIOD_YEAR_TO_DATE, "Year to date"),
    (PERIOD_YEAR, "Last 12 months"),
    (PERIOD_ALL, "All time"),
    (PERIOD_CUSTOM, "Custom range"),
)


def normalize_date_period(period):
    """Return a supported reporting date period."""
    return period if period in DATE_PERIODS else DEFAULT_DATE_PERIOD


def shift_months(value, months):
    """Return a date shifted by whole calendar months."""
    month_index = (value.year * 12) + (value.month - 1) + months
    year = month_index // 12
    month = (month_index % 12) + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def shift_years(value, years):
    """Return a date shifted by whole calendar years."""
    year = value.year + years
    day = min(value.day, monthrange(year, value.month)[1])
    return value.replace(year=year, day=day)


def period_start_date(period, today=None):
    """Return the inclusive start date for a reporting period."""
    period = normalize_date_period(period)
    today = today or date.today()

    if period == PERIOD_WEEK:
        return today - timedelta(days=7)

    if period == PERIOD_MONTH:
        return shift_months(today, -1)

    if period == PERIOD_90_DAYS:
        return today - timedelta(days=90)

    if period == PERIOD_6_MONTHS:
        return shift_months(today, -6)

    if period == PERIOD_YEAR_TO_DATE:
        return date(today.year, 1, 1)

    if period == PERIOD_YEAR:
        return shift_years(today, -1)

    return None


def previous_period_date_range(period, today=None):
    """Return the previous-period inclusive start and exclusive end dates."""
    period = normalize_date_period(period)
    today = today or date.today()

    if period == PERIOD_WEEK:
        return today - timedelta(days=14), today - timedelta(days=7)

    if period == PERIOD_MONTH:
        return shift_months(today, -2), shift_months(today, -1)

    if period == PERIOD_90_DAYS:
        return today - timedelta(days=180), today - timedelta(days=90)

    if period == PERIOD_6_MONTHS:
        return shift_months(today, -12), shift_months(today, -6)

    if period == PERIOD_YEAR_TO_DATE:
        return date(today.year - 1, 1, 1), shift_years(today, -1) + timedelta(days=1)

    if period == PERIOD_YEAR:
        return shift_years(today, -2), shift_years(today, -1)

    return None, None


def parse_iso_date(value):
    """Parse an ISO date string into the canonical date format."""
    value = str(value or "").strip()
    if not value:
        return ""

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return ""

    return value


def format_date_label(value):
    """Format an ISO date for display in page labels."""
    value = parse_iso_date(value)
    if not value:
        return ""

    date_obj = datetime.strptime(value, "%Y-%m-%d")
    return f"{date_obj.day:02d}-{month_abbreviation(date_obj.month)}-{date_obj.year}"


def get_period_label(period, date_from="", date_to=""):
    """Return a human-readable label for a selected reporting period."""
    if period == PERIOD_CUSTOM:
        from_label = format_date_label(date_from)
        to_label = format_date_label(date_to)
        if from_label and to_label:
            return gettext("{from_date} to {to_date}", from_date=from_label, to_date=to_label)
        if from_label:
            return gettext("from {date}", date=from_label)
        if to_label:
            return gettext("through {date}", date=to_label)
        return gettext("custom range")

    labels = dict(DATE_PERIOD_OPTIONS)
    return gettext(labels.get(period, labels[DEFAULT_DATE_PERIOD]))
