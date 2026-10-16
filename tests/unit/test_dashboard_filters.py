"""Tests for dashboard filter parsing helpers."""

from datetime import date

import pytest
from werkzeug.datastructures import MultiDict

from finance_app.modules.dashboard.filters import parse_dashboard_request
from finance_app.modules.dashboard.service import dashboard_previous_period_bounds


@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        ("2026-05-01", "2026-05-31", (date(2026, 3, 31), date(2026, 5, 1))),
        ("2024-03-01", "2024-03-01", (date(2024, 2, 29), date(2024, 3, 1))),
        ("", "2026-05-31", (None, None)),
    ],
)
def test_dashboard_previous_period_uses_equal_length_nonoverlapping_custom_range(date_from, date_to, expected):
    """Custom comparisons preserve inclusive duration while excluding the current period's first day."""
    request = parse_dashboard_request(MultiDict({"period": "custom", "date_from": date_from, "date_to": date_to}))

    assert dashboard_previous_period_bounds(request) == expected


def test_parse_dashboard_request_normalizes_query_controls():
    """Verify dashboard query parsing is centralized and deterministic."""
    parsed = parse_dashboard_request(
        MultiDict(
            [
                ("period", "custom"),
                ("date_from", "2026-02-28"),
                ("date_to", "2026-01-01"),
                ("filter_mode", "bad"),
                ("categories", " Food "),
                ("categories", ""),
                ("tags", "Tax"),
                ("merchant_id", "7"),
                ("merchant_query", "  metro   grocery "),
                ("account_id", "42"),
                ("quick_view", "unknown"),
            ]
        )
    )

    assert parsed.period == "custom"
    assert parsed.date_from == "2026-01-01"
    assert parsed.date_to == "2026-02-28"
    assert parsed.selected_account_id == 42
    assert parsed.selected_merchant_id == 7
    assert parsed.merchant_query == "metro grocery"
    assert parsed.merchant_search == "metro grocery"
    assert parsed.quick_view == "categorized"
