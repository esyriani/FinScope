"""Route-level tests for comparison pages."""

from datetime import date as real_date

from sqlalchemy import text
from tests.support.html import (
    assert_has_element,
    assert_markup,
    assert_not_visible_text,
    assert_option,
    assert_visible_text,
)

from finance_app.core.constants import TRANSACTION_KIND_INCOME
from finance_app.modules.comparison import service as comparison_service


class FixedDate(real_date):
    """Fixed replacement for date.today in comparison route tests."""

    @classmethod
    def today(cls):
        """Return a deterministic current date."""
        return cls(2026, 5, 11)


def test_comparison_route_renders_monthly_spending_distribution(owner_client, core_conn, monkeypatch):
    """Verify comparison renders yearly monthly-spending distribution in both languages."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, 'Food', 'rule', :p3)
        """),
        [
            {"p0": "2025-01-02", "p1": "Prior grocery", "p2": 50.00, "p3": "comparison-stats-2025-jan"},
            {"p0": "2025-02-02", "p1": "Prior grocery", "p2": 150.00, "p3": "comparison-stats-2025-feb"},
            {"p0": "2026-01-02", "p1": "Current grocery", "p2": 120.00, "p3": "comparison-stats-2026-jan"},
        ],
    )
    core_conn.commit()

    response = owner_client.get("/comparison?years=2025&years=2026")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Monthly spending distribution",
        "Boxplot summarizes observed monthly spending totals for each selected year.",
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

    french_response = owner_client.get("/comparison?years=2025&years=2026")

    assert french_response.status_code == 200
    assert_visible_text(
        french_response,
        "Distribution mensuelle : d\u00e9penses",
        "La bo\u00eete \u00e0 moustaches r\u00e9sume les totaux mensuels observ\u00e9s de d\u00e9penses",
    )
    assert_not_visible_text(
        french_response,
        "Statistiques des d\u00e9penses mensuelles",
        "Les statistiques utilisent les totaux mensuels observ\u00e9s",
        "Mois observ\u00e9s",
        "Monthly spending statistics",
    )


def test_comparison_route_renders_monthly_spending_table_option(owner_client, core_conn, monkeypatch):
    """Verify yearly monthly totals can be viewed as an exportable table."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, 'Food', 'rule', :p3)
        """),
        [
            {"p0": "2025-01-02", "p1": "Prior grocery", "p2": 50.00, "p3": "comparison-table-2025-jan"},
            {"p0": "2026-01-02", "p1": "Current grocery", "p2": 120.00, "p3": "comparison-table-2026-jan"},
            {"p0": "2026-02-02", "p1": "Current grocery", "p2": 80.00, "p3": "comparison-table-2026-feb"},
        ],
    )
    core_conn.commit()

    response = owner_client.get("/comparison?comparison_view=year&years=2025&years=2026&baseline_year=2025")

    assert response.status_code == 200
    assert_has_element(
        response,
        "section",
        attrs={
            "data-chart-export-scope": True,
            "data-table-export-scope": True,
            "data-comparison-monthly-visualization": True,
            "data-comparison-monthly-view": "line",
        },
    )
    assert_has_element(
        response,
        "div",
        attrs={"role": "group", "aria-label": "Monthly spending visualization"},
    )
    assert_has_element(response, "input", attrs={"id": "comparison_chart_table", "value": "table"})
    assert_has_element(response, "label", attrs={"for": "comparison_chart_table"}, text="Table")
    assert_has_element(
        response,
        "div",
        attrs={
            "id": "comparisonChart",
            "data-chart-export": True,
            "data-export-title": "Monthly spending by year",
        },
    )
    assert_has_element(response, "div", attrs={"id": "comparisonMonthlyTable", "hidden": True})
    assert_has_element(
        response,
        "table",
        attrs={
            "id": "comparisonMonthlyByYearTable",
            "data-export-title": "Monthly spending by year",
        },
        text="Jan 50.00 $ baseline 120.00 $ +70.00 $ | +140.0% 170.00 $",
    )
    assert_has_element(
        response,
        "span",
        attrs={"data-export-part": True, "data-export-type": "money", "data-export-value": "120.0"},
        text="120.00 $",
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-label": "Change",
            "data-export-type": "money",
            "data-export-value": "70.0",
        },
        text="+70.00 $",
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-label": "Percent",
            "data-export-type": "percent",
            "data-export-value": "1.4",
        },
        text="+140.0%",
    )
    assert_has_element(
        response,
        "span",
        attrs={"data-export-part": True, "data-export-type": "money", "data-export-value": "200.0"},
        text="200.00 $",
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-label": "Change",
            "data-export-type": "money",
            "data-export-value": "150.0",
        },
        text="+150.00 $",
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-label": "Percent",
            "data-export-type": "percent",
            "data-export-value": "3.0",
        },
        text="+300.0%",
    )


def test_comparison_route_uses_period_and_year_tabs(owner_client, core_conn, monkeypatch):
    """Verify comparison separates period changes from yearly trends with tabs."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, 'Food', 'rule', :p3)
        """),
        [
            {"p0": "2025-01-02", "p1": "Prior grocery", "p2": 50.00, "p3": "comparison-tabs-2025"},
            {"p0": "2026-05-02", "p1": "Current grocery", "p2": 120.00, "p3": "comparison-tabs-2026"},
        ],
    )
    core_conn.commit()

    response = owner_client.get("/comparison")

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

    year_response = owner_client.get("/comparison?comparison_view=year")

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
        attrs={"aria-labelledby": "comparison-year-filters-heading"},
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
        attrs={
            "id": "comparison-year-category-table-block",
            "aria-labelledby": "comparison-year-category-table-heading",
        },
        text="Category table",
    )


def test_comparison_route_renders_income_analysis_mode(owner_client, core_conn, monkeypatch):
    """Verify comparison renders and preserves the selected analysis mode."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            transaction_kind, fingerprint
        )
        VALUES (:p0, :p1, :p2, 'Income', 'rule', :p3, :p4)
        """),
        [
            {
                "p0": "2025-05-02",
                "p1": "Prior payroll",
                "p2": -900.00,
                "p3": TRANSACTION_KIND_INCOME,
                "p4": "comparison-income-route-2025",
            },
            {
                "p0": "2026-05-02",
                "p1": "Current payroll",
                "p2": -1200.00,
                "p3": TRANSACTION_KIND_INCOME,
                "p4": "comparison-income-route-2026",
            },
        ],
    )
    core_conn.commit()

    response = owner_client.get("/comparison?analysis_mode=income&comparison_view=year&years=2025&years=2026")

    assert response.status_code == 200
    assert_has_element(response, "select", attrs={"id": "comparison-year-analysis", "name": "analysis_mode"})
    assert_has_element(response, "select", attrs={"id": "comparison-period-analysis", "name": "analysis_mode"})
    assert_option(response, value="income", text="Income and credits", selected=True)
    assert_visible_text(
        response,
        "Analysis: income and credits",
        "Monthly income and credits by year",
        "Monthly income and credits distribution",
        "Category income and credits by year",
    )
    assert_markup(response, '"monthlyDistributionLabel": "Monthly income and credits distribution"')
