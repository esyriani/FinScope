"""Unit tests for shared template formatting filters."""

from flask import g

from finance_app.core.filters import format_date, format_datetime, format_percent


def test_format_date_uses_language_specific_shape():
    """Format dates with the selected UI language."""
    assert format_date("2026-09-12", language="en") == "Sep 12, 2026"
    assert format_date("2026-09-12", language="fr") == "12 sept. 2026"


def test_format_datetime_uses_english_timestamp_shape():
    """Format timestamps with English Canadian date ordering."""
    assert format_datetime("2026-05-13T03:38:00Z", timezone_name="UTC", language="en") == "May 13, 2026 03:38:00"


def test_format_datetime_converts_utc_to_configured_timezone():
    """Convert canonical UTC timestamps into the requested display timezone."""
    assert (
        format_datetime("2026-05-13T03:38:00Z", timezone_name="America/Toronto", language="en")
        == "May 12, 2026 23:38:00"
    )
    assert (
        format_datetime("2026-01-13T03:38:00Z", timezone_name="America/Toronto", language="en")
        == "Jan 12, 2026 22:38:00"
    )


def test_format_datetime_uses_french_timestamp_shape():
    """Format timestamps with French Canadian date ordering."""
    assert format_datetime("2026-05-13T03:38:00Z", timezone_name="UTC", language="fr") == "13 mai 2026 03:38:00"


def test_format_datetime_falls_back_to_utc_for_invalid_timezone():
    """Use UTC display when the configured timezone name is invalid."""
    assert (
        format_datetime("2026-05-13T03:38:00Z", timezone_name="Invalid/Timezone", language="en")
        == "May 13, 2026 03:38:00"
    )


def test_format_percent_uses_language_specific_spacing(app):
    """Format percentages with locale decimal and percent spacing."""
    with app.test_request_context():
        assert format_percent("12.34", signed=True) == "+12.3%"
        g.ui_language = "fr"
        assert format_percent("12.34", signed=True) == "+12,3 %"
