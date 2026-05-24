"""Unit tests for shared template formatting filters."""

from finance_app.core.filters import format_datetime


def test_format_datetime_uses_numeric_timestamp_shape():
    """Format timestamps as sortable numeric date-time strings."""
    assert format_datetime("2026-05-13T03:38:00Z", timezone_name="UTC") == "2026-05-13 03:38:00"


def test_format_datetime_converts_utc_to_configured_timezone():
    """Convert canonical UTC timestamps into the requested display timezone."""
    assert format_datetime("2026-05-13T03:38:00Z", timezone_name="America/Toronto") == "2026-05-12 23:38:00"
    assert format_datetime("2026-01-13T03:38:00Z", timezone_name="America/Toronto") == "2026-01-12 22:38:00"


def test_format_datetime_falls_back_to_utc_for_invalid_timezone():
    """Use UTC display when the configured timezone name is invalid."""
    assert format_datetime("2026-05-13T03:38:00Z", timezone_name="Invalid/Timezone") == "2026-05-13 03:38:00"
