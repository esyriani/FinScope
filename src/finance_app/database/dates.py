"""SQLAlchemy date and timestamp helpers.

Provides portable SQLAlchemy type decorators and date-part expressions used by
table metadata and reporting queries. The decorators keep the application
surface on ISO strings while binding typed values for databases that support
date and timestamp columns.
"""

from datetime import date, datetime, time, timezone

from sqlalchemy import Date, DateTime, Integer, String, cast, extract
from sqlalchemy.types import TypeDecorator


class ISODate(TypeDecorator):
    """Persist ISO dates through SQLAlchemy while returning ISO strings."""

    impl = Date
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Use native dates outside SQLite and ISO text for SQLite storage."""
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(10))
        return dialect.type_descriptor(Date())

    def process_bind_param(self, value, dialect):
        """Coerce incoming ISO strings or date objects for database binding."""
        coerced = coerce_date(value)
        if coerced is None:
            return None
        if dialect.name == "sqlite":
            return coerced.isoformat()
        return coerced

    def process_result_value(self, value, dialect):
        """Return database date values in the app's canonical ISO form."""
        del dialect
        return format_iso_date(value)


class UTCDateTime(TypeDecorator):
    """Persist UTC timestamps through SQLAlchemy while returning ISO strings."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Use native timestamps outside SQLite and ISO text for SQLite storage."""
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(32))
        return dialect.type_descriptor(DateTime(timezone=False))

    def process_bind_param(self, value, dialect):
        """Coerce incoming ISO strings or datetimes for database binding."""
        coerced = coerce_utc_datetime(value)
        if coerced is None:
            return None
        if dialect.name == "sqlite":
            return format_utc_datetime(coerced)
        return coerced

    def process_result_value(self, value, dialect):
        """Return database timestamp values in canonical UTC ISO form."""
        del dialect
        return format_utc_datetime(value)


def coerce_date(value):
    """Return a date from an ISO string, date, or datetime value."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def coerce_utc_datetime(value):
    """Return a naive UTC datetime from an ISO string, date, or datetime value."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def format_iso_date(value):
    """Return a canonical ISO date string from a date-like value."""
    coerced = coerce_date(value)
    return None if coerced is None else coerced.isoformat()


def format_utc_datetime(value):
    """Return a canonical UTC timestamp string from a datetime-like value."""
    coerced = coerce_utc_datetime(value)
    return None if coerced is None else f"{coerced.isoformat()}Z"


def date_year(column):
    """Return a SQLAlchemy expression for the year of a date column."""
    return cast(extract("year", column), Integer)


def date_month(column):
    """Return a SQLAlchemy expression for the month of a date column."""
    return cast(extract("month", column), Integer)


def date_month_identity(column):
    """Return a sortable numeric year-month identity for a date column."""
    return (date_year(column) * 100) + date_month(column)


def month_label(year, month):
    """Return a YYYY-MM month label from numeric year and month parts."""
    return f"{int(year):04d}-{int(month):02d}"
