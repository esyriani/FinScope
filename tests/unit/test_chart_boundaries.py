"""Static ownership checks for shared browser chart infrastructure.

Formatting and escaping behavior are executed by the frontend runtime tests;
these checks only guard against duplicating infrastructure in page scripts.
"""

from pathlib import Path

STATIC_JS = Path(__file__).resolve().parents[2] / "src" / "finance_app" / "static" / "js"


def test_browser_money_formatting_is_centralized():
    """Keep locale and currency formatting in the shared application boot layer."""
    for script_name in ("dashboard-charts.js", "comparison-charts.js", "calendar.js", "recurring.js"):
        script = (STATIC_JS / script_name).read_text(encoding="utf-8")
        assert "new Intl.NumberFormat" not in script
        assert "window.financeFormatMoney" in script or ".formatMoney" in script


def test_page_chart_modules_do_not_duplicate_shared_infrastructure():
    """Keep chart data reading, theming and resize observation in the common helper."""
    for script_name in ("dashboard-charts.js", "comparison-charts.js", "reports-charts.js"):
        script = (STATIC_JS / script_name).read_text(encoding="utf-8")
        assert "window.financeCharts" in script
        assert "function readJsonScript" not in script
        assert "getComputedStyle(document.documentElement)" not in script
        assert "new ResizeObserver" not in script
