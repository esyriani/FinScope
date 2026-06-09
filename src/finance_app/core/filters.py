"""Template value formatting helpers.

Provides shared Jinja filters for dates, timestamps, and money values.
Timestamp presentation uses the configured application timezone.
"""

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from finance_app.core import config as config_module
from finance_app.core.i18n import month_abbreviation
from finance_app.core.money import MoneyValue, format_money_display
from finance_app.database.dates import coerce_utc_datetime

LOCAL_TIMEZONE_FALLBACKS = {"local", "system"}
EASTERN_TIMEZONE_FALLBACKS = {
    "America/Toronto",
    "America/New_York",
    "Canada/Eastern",
    "US/Eastern",
}
EASTERN_STANDARD_OFFSET = timedelta(hours=-5)
EASTERN_DAYLIGHT_OFFSET = timedelta(hours=-4)


class EasternTimeFallback(tzinfo):
    """Represent modern Eastern time when IANA timezone data is unavailable.

    The fallback follows the post-2007 North American daylight saving rules.
    It is used only when ``zoneinfo`` cannot load the configured Eastern IANA
    timezone, which can happen on Windows before the ``tzdata`` package is
    installed.
    """

    def utcoffset(self, dt: datetime | None) -> timedelta:
        """Return the UTC offset for a local Eastern datetime."""
        return EASTERN_STANDARD_OFFSET + self.dst(dt)

    def dst(self, dt: datetime | None) -> timedelta:
        """Return the daylight saving offset for a local Eastern datetime."""
        if dt is None:
            return timedelta(0)

        local_value = dt.replace(tzinfo=None)
        start = eastern_dst_start_local(local_value.year)
        end = eastern_dst_end_local(local_value.year)
        return timedelta(hours=1) if start <= local_value < end else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        """Return the Eastern timezone abbreviation for a local datetime."""
        return "EDT" if self.dst(dt) else "EST"

    def fromutc(self, dt: datetime) -> datetime:
        """Convert a UTC datetime into Eastern time using DST transition UTC instants."""
        if dt.tzinfo is not self:
            raise ValueError("fromutc: dt.tzinfo is not self")

        utc_value = dt.replace(tzinfo=None)
        start = eastern_dst_start_utc(utc_value.year)
        end = eastern_dst_end_utc(utc_value.year)
        offset = EASTERN_DAYLIGHT_OFFSET if start <= utc_value < end else EASTERN_STANDARD_OFFSET
        return (utc_value + offset).replace(tzinfo=self)


EASTERN_TIME_FALLBACK = EasternTimeFallback()


def nth_weekday_of_month(year: int, month: int, weekday: int, occurrence: int) -> int:
    """Return the date number for a repeated weekday in a month.

    Args:
        year: Four-digit year.
        month: One-based month number.
        weekday: Python weekday number where Monday is 0.
        occurrence: One-based occurrence of the weekday within the month.

    Returns:
        The day of the month for the requested weekday occurrence.
    """
    first_day = datetime(year, month, 1)
    days_until_weekday = (weekday - first_day.weekday()) % 7
    return 1 + days_until_weekday + ((occurrence - 1) * 7)


def eastern_dst_start_local(year: int) -> datetime:
    """Return the local Eastern datetime when daylight saving time starts."""
    day = nth_weekday_of_month(year, 3, 6, 2)
    return datetime(year, 3, day, 2)


def eastern_dst_end_local(year: int) -> datetime:
    """Return the local Eastern datetime when daylight saving time ends."""
    day = nth_weekday_of_month(year, 11, 6, 1)
    return datetime(year, 11, day, 2)


def eastern_dst_start_utc(year: int) -> datetime:
    """Return the UTC datetime when Eastern daylight saving time starts."""
    return eastern_dst_start_local(year) - EASTERN_STANDARD_OFFSET


def eastern_dst_end_utc(year: int) -> datetime:
    """Return the UTC datetime when Eastern daylight saving time ends."""
    return eastern_dst_end_local(year) - EASTERN_DAYLIGHT_OFFSET


def format_date(value: object) -> str:
    """Format an ISO date value for template display."""
    if not value:
        return ""

    date_obj = datetime.strptime(str(value), "%Y-%m-%d")
    return f"{date_obj.day:02d}-{month_abbreviation(date_obj.month)}-{date_obj.year}"


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
        names fall back to UTC. Eastern IANA names use a small DST-aware
        fallback if IANA data is unavailable on Windows.
    """
    utc_value = value.replace(tzinfo=timezone.utc)
    name = configured_timezone_name(timezone_name)
    if name.upper() == "UTC":
        return utc_value

    try:
        return utc_value.astimezone(ZoneInfo(name))
    except ZoneInfoNotFoundError:
        if name in EASTERN_TIMEZONE_FALLBACKS:
            return utc_value.astimezone(EASTERN_TIME_FALLBACK)
        if name in LOCAL_TIMEZONE_FALLBACKS:
            return utc_value.astimezone()
        return utc_value


def format_datetime(value: object, timezone_name: object | None = None) -> str:
    """Format a UTC date-time value in the configured display timezone.

    Args:
        value: A UTC ISO timestamp, ``datetime``, or ``date`` value.
        timezone_name: Optional IANA timezone override, mainly for tests.

    Returns:
        A timestamp string formatted as ``YYYY-MM-DD HH:MM:SS``.
    """
    if not value:
        return ""

    date_obj = coerce_utc_datetime(value)
    assert date_obj is not None
    display_datetime = localize_utc_datetime(date_obj, timezone_name)
    return display_datetime.strftime("%Y-%m-%d %H:%M:%S")


def format_money(value: MoneyValue | None) -> str:
    """Format a money value for template display."""
    return format_money_display(value)


def register_filters(app: Any) -> None:
    """Register shared Jinja value formatting filters."""
    app.jinja_env.filters["datefmt"] = format_date
    app.jinja_env.filters["datetimefmt"] = format_datetime
    app.jinja_env.filters["moneyfmt"] = format_money
