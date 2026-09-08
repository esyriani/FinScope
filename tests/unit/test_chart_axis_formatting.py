"""Static checks for chart money axis formatting."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_and_comparison_money_axes_use_whole_dollars():
    """Verify money axes use whole-dollar formatters while tooltip formatters can keep cents."""
    chart_utils_js = (ROOT / "src" / "finance_app" / "static" / "js" / "chart-utils.js").read_text(encoding="utf-8")
    dashboard_js = (ROOT / "src" / "finance_app" / "static" / "js" / "dashboard-charts.js").read_text(encoding="utf-8")
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
    app_boot_js = (ROOT / "src" / "finance_app" / "static" / "js" / "app-boot.js").read_text(encoding="utf-8")
    chart_utils_js = (ROOT / "src" / "finance_app" / "static" / "js" / "chart-utils.js").read_text(encoding="utf-8")

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
        script = (ROOT / "src" / "finance_app" / "static" / "js" / script_name).read_text(encoding="utf-8")
        assert "new Intl.NumberFormat" not in script
        assert 'replace(/,/g, " ")' not in script
        assert ' + " $"' not in script
        assert "window.financeFormatMoney" in script or ".formatMoney" in script


def test_browser_money_formatter_preserves_locale_decimal_separators():
    """Verify browser money display only normalizes grouping separators."""
    app_boot_js = (ROOT / "src" / "finance_app" / "static" / "js" / "app-boot.js").read_text(encoding="utf-8")

    assert "function financeMoneyNumber(value)" in app_boot_js
    assert 'typeof value !== "number" && typeof value !== "string"' in app_boot_js
    assert 'typeof value === "string" && value.trim() === ""' in app_boot_js
    assert "Number.isFinite(numberValue) ? numberValue : null" in app_boot_js
    assert "function financeFormatNumberParts(formatter, numberValue)" in app_boot_js
    assert ".formatToParts(numberValue)" in app_boot_js
    assert 'part.type === "group" ? " " : part.value' in app_boot_js
    assert 'replace(/,/g, " ")' not in app_boot_js
    assert "Number(value) || 0" not in app_boot_js


def test_browser_money_fallbacks_do_not_coerce_missing_values_to_zero():
    """Verify browser money fallbacks do not hide malformed values as zero."""
    for script_name in [
        "chart-utils.js",
        "calendar.js",
        "recurring.js",
        "reimbursements.js",
    ]:
        script = (ROOT / "src" / "finance_app" / "static" / "js" / script_name).read_text(encoding="utf-8")
        assert "Number(value || 0).toFixed(2)" not in script
        assert "value === null || value === undefined" in script
        assert 'typeof value !== "number" && typeof value !== "string"' in script
        assert 'typeof value === "string" && value.trim() === ""' in script
        assert 'return numberValue === null ? "" : numberValue.toFixed(2)' in script

    chart_utils_js = (ROOT / "src" / "finance_app" / "static" / "js" / "chart-utils.js").read_text(encoding="utf-8")
    assert 'return numberValue === null ? "" : String(Math.round(numberValue))' in chart_utils_js


def test_dashboard_and_comparison_chart_helpers_are_shared():
    """Verify chart modules use financeCharts for common chart infrastructure."""
    chart_utils_js = (ROOT / "src" / "finance_app" / "static" / "js" / "chart-utils.js").read_text(encoding="utf-8")

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
        "forceResize",
        "resize",
    ]:
        assert helper_name in chart_utils_js

    for script_name in ["dashboard-charts.js", "comparison-charts.js"]:
        script = (ROOT / "src" / "finance_app" / "static" / "js" / script_name).read_text(encoding="utf-8")

        assert "window.financeCharts" in script
        assert "function readJsonScript" not in script
        assert "getComputedStyle(document.documentElement)" not in script
        assert 'window.addEventListener("resize"' not in script
        assert "new ResizeObserver" not in script


def test_shared_chart_helper_refreshes_after_late_layout_events():
    """Verify ECharts canvases are refreshed after tab, page, and font layout settles."""
    chart_utils_js = (ROOT / "src" / "finance_app" / "static" / "js" / "chart-utils.js").read_text(encoding="utf-8")

    assert "function financeChartCanResize(chart)" in chart_utils_js
    assert "getBoundingClientRect()" in chart_utils_js
    assert "refreshImmediately" in chart_utils_js
    assert "renderer?.refresh?.()" in chart_utils_js
    assert 'window.addEventListener("finance:layoutchange", resizeHandler)' in chart_utils_js
    assert 'window.addEventListener("pageshow", resizeHandler)' in chart_utils_js
    assert 'window.addEventListener("load", resizeHandler, { once: true })' in chart_utils_js
    assert "document.fonts?.ready?.then(resizeHandler)" in chart_utils_js


def test_chart_tooltips_escape_interpolated_labels():
    """Verify chart tooltip HTML escapes labels supplied from report data."""
    chart_utils_js = (ROOT / "src" / "finance_app" / "static" / "js" / "chart-utils.js").read_text(encoding="utf-8")
    reports_js = (ROOT / "src" / "finance_app" / "static" / "js" / "reports-charts.js").read_text(encoding="utf-8")
    dashboard_js = (ROOT / "src" / "finance_app" / "static" / "js" / "dashboard-charts.js").read_text(encoding="utf-8")
    comparison_js = (ROOT / "src" / "finance_app" / "static" / "js" / "comparison-charts.js").read_text(
        encoding="utf-8"
    )

    assert "function financeChartEscapeHtml(value)" in chart_utils_js
    assert '.replace(/&/g, "&amp;")' in chart_utils_js
    assert '.replace(/</g, "&lt;")' in chart_utils_js
    assert '.replace(/>/g, "&gt;")' in chart_utils_js
    assert 'function financeChartTooltipLine(label, value, marker = "")' in chart_utils_js
    assert "escapeHtml: financeChartEscapeHtml" in chart_utils_js
    assert "tooltipLine: financeChartTooltipLine" in chart_utils_js
    assert "reportsTooltipLine(item.seriesName, formatReportsMoney(item.value), item.marker)" in reports_js
    assert "reportsChartEscapeHtml(items[0]?.axisValue)" in reports_js
    assert "reportsTooltipLine(params.name, formatReportsMoney(params.value))" in reports_js
    assert "dashboardTooltipLine(item.seriesName, formatDashboardMoney(item.value), item.marker)" in dashboard_js
    assert "comparisonTooltipLine(item.seriesName, formatComparisonMoney(item.value), item.marker)" in comparison_js

    for script in (reports_js, dashboard_js, comparison_js):
        assert "`${params.name}: ${format" not in script
        assert "`${item.marker}${item.seriesName}: ${format" not in script


def test_dashboard_quality_panel_links_are_normal_click_targets():
    """Verify dashboard quality actions are not captured by drill-down selection."""
    dashboard_js = (ROOT / "src" / "finance_app" / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert ".quality-panel a[href]" not in dashboard_js
