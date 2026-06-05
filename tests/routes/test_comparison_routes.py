"""Route-level tests for comparison pages."""

from datetime import date as real_date

from sqlalchemy import text

from finance_app.modules.comparison import service as comparison_service
from tests.support.html import assert_markup, assert_not_visible_text, assert_visible_text


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
