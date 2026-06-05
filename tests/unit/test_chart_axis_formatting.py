"""Static checks for chart money axis formatting."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_and_comparison_money_axes_use_whole_dollars():
    """Verify money axes use whole-dollar formatters while tooltip formatters can keep cents."""
    chart_utils_js = (ROOT / "src" / "finance_app" / "static" / "js" / "chart-utils.js").read_text(
        encoding="utf-8"
    )
    dashboard_js = (ROOT / "src" / "finance_app" / "static" / "js" / "dashboard-charts.js").read_text(
        encoding="utf-8"
    )
    comparison_js = (ROOT / "src" / "finance_app" / "static" / "js" / "comparison-charts.js").read_text(
        encoding="utf-8"
    )

    assert "formatDashboardAxisMoney" in dashboard_js
    assert "formatComparisonAxisMoney" in comparison_js
    assert "window.financeFormatAxisMoney" in chart_utils_js
    assert ".formatAxisMoney" in dashboard_js
    assert ".formatAxisMoney" in comparison_js
    assert "axisLabel: dashboardAxisLabel(formatDashboardMoney)" not in dashboard_js
    assert "axisLabel: comparisonAxisLabel(formatComparisonMoney)" not in comparison_js


def test_browser_money_formatting_is_centralized():
    """Verify browser money display reads the shared configured-currency formatter."""
    app_boot_js = (ROOT / "src" / "finance_app" / "static" / "js" / "app-boot.js").read_text(
        encoding="utf-8"
    )
    chart_utils_js = (ROOT / "src" / "finance_app" / "static" / "js" / "chart-utils.js").read_text(
        encoding="utf-8"
    )

    assert "window.financeCurrencySymbol" in app_boot_js
    assert "window.financeFormatMoney" in app_boot_js
    assert "window.financeCharts" in chart_utils_js
    assert "window.financeFormatMoney" in chart_utils_js

    for script_name in [
        "dashboard-charts.js",
        "comparison-charts.js",
        "calendar.js",
        "recurring.js",
    ]:
        script = (ROOT / "src" / "finance_app" / "static" / "js" / script_name).read_text(
            encoding="utf-8"
        )
        assert "new Intl.NumberFormat" not in script
        assert 'replace(/,/g, " ")' not in script
        assert ' + " $"' not in script
        assert "window.financeFormatMoney" in script or ".formatMoney" in script


def test_dashboard_and_comparison_chart_helpers_are_shared():
    """Verify chart modules use financeCharts for common chart infrastructure."""
    chart_utils_js = (ROOT / "src" / "finance_app" / "static" / "js" / "chart-utils.js").read_text(
        encoding="utf-8"
    )

    for helper_name in [
        "readJsonScript",
        "palette",
        "theme",
        "axisLine",
        "axisLabel",
        "splitLine",
        "tooltip",
        "baseGrid",
        "create",
        "resize",
    ]:
        assert helper_name in chart_utils_js

    for script_name in ["dashboard-charts.js", "comparison-charts.js"]:
        script = (ROOT / "src" / "finance_app" / "static" / "js" / script_name).read_text(
            encoding="utf-8"
        )

        assert "window.financeCharts" in script
        assert "function readJsonScript" not in script
        assert "getComputedStyle(document.documentElement)" not in script
        assert 'window.addEventListener("resize"' not in script
        assert "new ResizeObserver" not in script


def test_dashboard_quality_panel_links_are_normal_click_targets():
    """Verify dashboard quality actions are not captured by drill-down selection."""
    dashboard_js = (ROOT / "src" / "finance_app" / "static" / "js" / "dashboard.js").read_text(
        encoding="utf-8"
    )

    assert ".quality-panel a[href]" not in dashboard_js
