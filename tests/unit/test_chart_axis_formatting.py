"""Static checks for chart money axis formatting."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_and_comparison_money_axes_use_whole_dollars():
    """Verify money axes use whole-dollar formatters while tooltip formatters can keep cents."""
    dashboard_js = (ROOT / "src" / "finance_app" / "static" / "js" / "dashboard-charts.js").read_text(
        encoding="utf-8"
    )
    comparison_js = (ROOT / "src" / "finance_app" / "static" / "js" / "comparison-charts.js").read_text(
        encoding="utf-8"
    )

    assert "formatDashboardAxisMoney" in dashboard_js
    assert "formatComparisonAxisMoney" in comparison_js
    assert "axisLabel: dashboardAxisLabel(formatDashboardMoney)" not in dashboard_js
    assert "axisLabel: comparisonAxisLabel(formatComparisonMoney)" not in comparison_js


def test_dashboard_quality_panel_links_are_normal_click_targets():
    """Verify dashboard quality actions are not captured by drill-down selection."""
    dashboard_js = (ROOT / "src" / "finance_app" / "static" / "js" / "dashboard.js").read_text(
        encoding="utf-8"
    )

    assert ".quality-panel a[href]" not in dashboard_js
