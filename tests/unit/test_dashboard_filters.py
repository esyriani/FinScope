"""Tests for dashboard filter parsing helpers."""

from werkzeug.datastructures import MultiDict

from finance_app.modules.dashboard.filters import parse_dashboard_request


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
    assert parsed.filter_mode == "include"
    assert parsed.selected_categories == ["Food"]
    assert parsed.selected_tags == ["Tax"]
    assert parsed.selected_account_id == 42
    assert parsed.selected_merchant_id == 7
    assert parsed.merchant_query == "metro grocery"
    assert parsed.merchant_search == "metro grocery"
    assert parsed.quick_view == "unknown"
