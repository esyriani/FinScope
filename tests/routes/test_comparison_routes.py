"""Route-level tests for comparison pages."""

from datetime import date as real_date

from sqlalchemy import text

from finance_app.modules.comparison import service as comparison_service
from tests.support.html import assert_has_element, assert_markup, assert_not_visible_text, assert_visible_text


class FixedDate(real_date):
    """Fixed replacement for date.today in comparison route tests."""

    @classmethod
    def today(cls):
        """Return a deterministic current date."""
        return cls(2026, 5, 11)


def test_comparison_route_renders_monthly_spending_distribution(client, core_conn, monkeypatch):
    """Verify comparison renders yearly monthly-spending distribution in both languages."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, 'Food', 'rule', :p3)
        """), [
            {"p0": "2025-01-02", "p1": "Prior grocery", "p2": 50.00, "p3": "comparison-stats-2025-jan"},
            {"p0": "2025-02-02", "p1": "Prior grocery", "p2": 150.00, "p3": "comparison-stats-2025-feb"},
            {"p0": "2026-01-02", "p1": "Current grocery", "p2": 120.00, "p3": "comparison-stats-2026-jan"},
        ])
    core_conn.commit()

    response = client.get("/comparison?years=2025&years=2026")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Monthly spending distribution",
        "Boxplot summarizes observed monthly totals for each selected year.",
    )
    assert_not_visible_text(
        response,
        "Monthly spending statistics",
        "Statistics use observed monthly spending totals for each selected year.",
        "Observed months",
        "STDEV",
    )
    assert_markup(
        response,
        'id="comparisonBoxplotChart"',
        '"monthlySpendingStatistics"',
        '"mean": 100.0',
        '"boxplot": [50.0, 75.0, 100.0, 125.0, 150.0]',
    )

    core_conn.execute(text("""
        UPDATE user_settings
        SET value = 'fr'
        WHERE key = 'ui_language'
          AND user_id = (SELECT id FROM users WHERE username = 'owner')
        """))
    core_conn.commit()

    french_response = client.get("/comparison?years=2025&years=2026")

    assert french_response.status_code == 200
    assert_visible_text(
        french_response,
        "Distribution des d\u00e9penses mensuelles",
        "La bo\u00eete \u00e0 moustaches r\u00e9sume les totaux mensuels observ\u00e9s",
    )
    assert_not_visible_text(
        french_response,
        "Statistiques des d\u00e9penses mensuelles",
        "Les statistiques utilisent les totaux mensuels observ\u00e9s",
        "Mois observ\u00e9s",
        "Monthly spending statistics",
    )


def test_comparison_route_uses_period_and_year_tabs(client, core_conn, monkeypatch):
    """Verify comparison separates period changes from yearly trends with tabs."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, 'Food', 'rule', :p3)
        """), [
            {"p0": "2025-01-02", "p1": "Prior grocery", "p2": 50.00, "p3": "comparison-tabs-2025"},
            {"p0": "2026-05-02", "p1": "Current grocery", "p2": 120.00, "p3": "comparison-tabs-2026"},
        ])
    core_conn.commit()

    response = client.get("/comparison")

    assert response.status_code == 200
    assert_visible_text(response, "Period changes", "Year trends")
    assert_has_element(
        response,
        "div",
        attrs={"class": "comparison-tabs", "role": "tablist", "aria-label": "Comparison views"},
    )
    assert_has_element(
        response,
        "button",
        attrs={
            "id": "comparison-period-tab",
            "role": "tab",
            "class": "active",
            "aria-selected": "true",
            "data-bs-target": "#period-comparison-section",
        },
        text="Period changes",
    )
    assert_has_element(
        response,
        "button",
        attrs={
            "id": "comparison-year-tab",
            "role": "tab",
            "aria-selected": "false",
            "data-bs-target": "#year-comparison-section",
        },
        text="Year trends",
    )
    assert_has_element(
        response,
        "section",
        attrs={"id": "period-comparison-section", "role": "tabpanel", "class": "active"},
    )
    assert_has_element(
        response,
        "section",
        attrs={"id": "comparison-summary-block", "aria-labelledby": "comparison-summary-heading"},
        text="Summary",
    )
    assert_has_element(
        response,
        "section",
        attrs={"id": "comparison-insights-block", "aria-labelledby": "comparison-insights-heading"},
        text="Key insights",
    )
    assert_has_element(
        response,
        "section",
        attrs={"id": "comparison-details-block", "aria-labelledby": "comparison-details-heading"},
        text="Details",
    )
    assert_has_element(response, "input", attrs={"name": "comparison_view", "value": "period"})
    assert_has_element(response, "input", attrs={"name": "comparison_view", "value": "year"})
    assert_has_element(
        response,
        "div",
        attrs={"class": "comparison-detail-tabs", "role": "tablist", "aria-label": "Period change details"},
    )
    assert_has_element(
        response,
        "button",
        attrs={
            "id": "comparison-category-changes-tab",
            "role": "tab",
            "class": "active",
            "aria-selected": "true",
            "data-bs-target": "#comparison-category-changes",
        },
        text="Category changes",
    )
    assert_has_element(
        response,
        "button",
        attrs={
            "id": "comparison-merchant-changes-tab",
            "role": "tab",
            "aria-selected": "false",
            "data-bs-target": "#comparison-merchant-changes",
        },
        text="Merchant changes",
    )
    assert_has_element(
        response,
        "section",
        attrs={"id": "comparison-category-changes", "role": "tabpanel", "class": "active"},
    )
    assert_has_element(
        response,
        "section",
        attrs={"id": "comparison-merchant-changes", "role": "tabpanel"},
    )

    year_response = client.get("/comparison?comparison_view=year")

    assert year_response.status_code == 200
    assert_has_element(
        year_response,
        "button",
        attrs={"id": "comparison-year-tab", "class": "active", "aria-selected": "true"},
    )
    assert_has_element(
        year_response,
        "section",
        attrs={"id": "year-comparison-section", "role": "tabpanel", "class": "active"},
    )
    assert_has_element(
        year_response,
        "section",
        attrs={"id": "comparison-year-filters-block", "aria-labelledby": "comparison-year-filters-heading"},
        text="Filters",
    )
    assert_has_element(
        year_response,
        "section",
        attrs={"id": "comparison-year-charts-block", "aria-labelledby": "comparison-year-charts-heading"},
        text="Charts",
    )
    assert_has_element(
        year_response,
        "section",
        attrs={"id": "comparison-year-category-table-block", "aria-labelledby": "comparison-year-category-table-heading"},
        text="Category table",
    )
