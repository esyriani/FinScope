"""Tests for shared feature URL builders."""

from urllib.parse import parse_qs, urlsplit

from flask import request

from finance_app.modules.calendar.urls import calendar_url
from finance_app.modules.calendar.urls import transactions_url as calendar_transactions_url
from finance_app.modules.comparison.urls import build_comparison_url
from finance_app.modules.dashboard.urls import dashboard_transactions_url
from finance_app.modules.reports.urls import build_reports_url, reports_url
from finance_app.modules.review.urls import build_review_sort_url, build_review_url
from finance_app.modules.transactions.urls import transactions_redirect_with_ignored, transactions_url


def parsed_query(url):
    """Return query parameters for a generated application URL."""
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def test_dashboard_transactions_url_builds_drilldown_query(app):
    """Verify dashboard drill-down URLs include current dimension filters."""
    with app.test_request_context("/dashboard"):
        url = dashboard_transactions_url(
            "custom",
            "include",
            ["Food"],
            date_from="2026-01-01",
            date_to="2026-01-31",
            selected_tags=["Tax"],
            merchant_search="metro grocery",
            account_id=7,
            amount_type="spending",
        )

    assert urlsplit(url).path == "/transactions"
    assert parsed_query(url) == {
        "period": ["custom"],
        "ignored": ["active"],
        "date_from": ["2026-01-01"],
        "date_to": ["2026-01-31"],
        "filter_mode": ["include"],
        "tags": ["Tax"],
        "categories": ["Food"],
        "search": ["metro grocery"],
        "account_id": ["7"],
        "amount_type": ["spending"],
    }


def test_transactions_url_preserves_query_values(app):
    """Verify transactions URLs preserve active filters and stringify override values."""
    with app.test_request_context("/transactions?period=all&ignored=active"):
        url = transactions_url(page=2, categories=["Food", ""], search="metro")

    assert urlsplit(url).path == "/transactions"
    assert parsed_query(url) == {
        "period": ["all"],
        "ignored": ["active"],
        "page": ["2"],
        "categories": ["Food"],
        "search": ["metro"],
    }


def test_transactions_redirect_with_ignored_replaces_filter(app):
    """Verify ignored redirect updates the filter without dropping other query parameters."""
    url = transactions_redirect_with_ignored("/transactions?period=all&ignored=active", "all")

    assert urlsplit(url).path == "/transactions"
    assert parsed_query(url) == {
        "period": ["all"],
        "ignored": ["all"],
    }


def test_comparison_url_removes_blank_query_values(app):
    """Verify comparison URLs omit empty filters."""
    with app.test_request_context("/comparison"):
        url = build_comparison_url(years=["2026", ""], categories=[], view="year", empty="")

    assert urlsplit(url).path == "/comparison"
    assert parsed_query(url) == {
        "years": ["2026"],
        "view": ["year"],
    }


def test_reports_url_removes_blank_query_values(app):
    """Verify Reports URLs omit empty filters."""
    with app.test_request_context("/reports"):
        url = build_reports_url("reports.income", basis="ledger_rows", account_id=None, empty="")

    assert urlsplit(url).path == "/reports/income"
    assert parsed_query(url) == {
        "basis": ["ledger_rows"],
    }


def test_reports_url_preserves_and_overrides_query_values(app):
    """Verify Reports URLs preserve current filters and stringify overrides."""
    with app.test_request_context("/reports?period=ytd&account_id=3&empty="):
        url = reports_url(request.args, measure="spending", empty="")

    assert urlsplit(url).path == "/reports"
    assert parsed_query(url) == {
        "period": ["ytd"],
        "account_id": ["3"],
        "measure": ["spending"],
    }


def test_review_urls_preserve_grouping_and_toggle_sort(app):
    """Verify review URLs include ungrouped keys and sort toggles."""
    with app.test_request_context("/review"):
        url = build_review_url(2, ["A", "B"], "merchant", "asc", merchant_search="metro")
        sort_url = build_review_sort_url("merchant", "merchant", "asc", ["A"])

    assert parsed_query(url) == {
        "page": ["2"],
        "sort": ["merchant"],
        "direction": ["asc"],
        "merchant": ["metro"],
        "ungroup": ["A", "B"],
    }
    assert parsed_query(sort_url) == {
        "page": ["1"],
        "sort": ["merchant"],
        "direction": ["desc"],
        "ungroup": ["A"],
    }


def test_calendar_urls_build_month_and_transaction_links(app):
    """Verify calendar URLs include month filters and transaction date scopes."""
    with app.test_request_context("/calendar"):
        month_url = calendar_url(month_bounds_date(), {"categories": ["Food", ""], "account_id": 7, "empty": ""})
        tx_url = calendar_transactions_url("2026-05-01", "2026-05-31", account_id=7)

    assert urlsplit(month_url).path == "/calendar"
    assert parsed_query(month_url) == {
        "month": ["2026-05"],
        "categories": ["Food"],
        "account_id": ["7"],
    }
    assert urlsplit(tx_url).path == "/transactions"
    assert parsed_query(tx_url) == {
        "period": ["custom"],
        "date_from": ["2026-05-01"],
        "date_to": ["2026-05-31"],
        "ignored": ["active"],
        "account_id": ["7"],
    }


def month_bounds_date():
    """Return a stable month date for calendar URL tests."""
    from datetime import date

    return date(2026, 5, 1)
