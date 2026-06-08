"""Static regression checks for frontend initializer wiring."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
STATIC_JS = ROOT / "src" / "finance_app" / "static" / "js"
STATIC_CSS = ROOT / "src" / "finance_app" / "static" / "css"
TEMPLATES = ROOT / "src" / "finance_app" / "templates"
SCRIPT_TAG_RE = re.compile(r"<script\b(?P<attrs>[^>]*)>", re.IGNORECASE)
CLIENT_TRANSLATION_RE = re.compile(
    r'\b(?:financeTranslate|translateJobsMessage|dashboardTranslate|translateTableMessage|translate|t)\(\s*"([^"]+)"'
)


def read_script(name):
    """Return the JavaScript source for a static app script."""
    return (STATIC_JS / name).read_text(encoding="utf-8")


def read_style(name):
    """Return the CSS source for a static app stylesheet."""
    return (STATIC_CSS / name).read_text(encoding="utf-8")


def test_ajax_refresh_uses_initializer_registry():
    """Verify AJAX refreshes use the shared registry instead of page-specific globals."""
    ajax_actions = read_script("ajax-actions.js")

    assert "window.financeApp?.runInitializers(root)" in ajax_actions
    assert "window.setupDashboardPage" not in ajax_actions
    assert "window.setupUploadPreview" not in ajax_actions
    assert "window.setupTableExports" not in ajax_actions


def test_static_scripts_do_not_export_setup_globals():
    """Verify page setup hooks register with financeApp instead of window.setup names."""
    core = read_script("core.js")

    assert "registerInitializer" in core
    assert "runInitializers" in core
    for script_path in STATIC_JS.glob("*.js"):
        assert "window.setup" not in script_path.read_text(encoding="utf-8")


def test_templates_do_not_include_executable_inline_scripts():
    """Verify templates keep executable behavior in static JavaScript files."""
    offenders = []
    for template_path in TEMPLATES.rglob("*.html"):
        template = template_path.read_text(encoding="utf-8")
        for match in SCRIPT_TAG_RE.finditer(template):
            attrs = match.group("attrs")
            if "src=" in attrs:
                continue
            if 'type="application/json"' in attrs:
                continue
            line_number = template[:match.start()].count("\n") + 1
            offenders.append(f"{template_path.relative_to(ROOT)}:{line_number}")

    assert offenders == []


def test_interactive_table_rows_have_keyboard_semantics():
    """Verify clickable table rows expose focus and keyboard activation behavior."""
    tables_js = read_script("tables.js")

    assert "row.tabIndex = 0" in tables_js
    assert 'row.setAttribute("role", "button")' in tables_js
    assert 'row.addEventListener("keydown"' in tables_js
    assert 'event.key !== "Enter" && event.key !== " "' in tables_js


def test_comparison_tabs_preserve_active_view_in_url():
    """Verify comparison top-level tab switches update the refreshable view query."""
    comparison_js = read_script("comparison.js")

    assert '"comparison-period-tab": "period"' in comparison_js
    assert '"comparison-year-tab": "year"' in comparison_js
    assert 'url.searchParams.set("comparison_view", view)' in comparison_js
    assert "window.history.replaceState" in comparison_js
    assert "updateComparisonViewQuery(tab)" in comparison_js


def test_comparison_insight_carousel_uses_responsive_three_card_grid():
    """Verify comparison insights show three cards on wide screens and adapt down."""
    comparison_css = read_style("comparison.css")

    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in comparison_css
    assert "@media (max-width: 1100px)" in comparison_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in comparison_css
    assert "grid-template-columns: 1fr;" in comparison_css


def test_dynamic_user_rows_avoid_inner_html_builders():
    """Verify dynamic user/import values are rendered through DOM APIs."""
    dynamic_scripts = ["rules.js", "recurring.js", "jobs.js", "upload.js", "calendar.js"]

    for script_name in dynamic_scripts:
        script = read_script(script_name)
        assert "innerHTML =" not in script
        assert ".replaceChildren(" in script


def test_client_translation_messages_cover_static_js_strings():
    """Verify direct browser translation strings are exposed to client i18n."""
    from finance_app import CLIENT_TRANSLATION_MESSAGES

    messages = {
        match.group(1)
        for script_path in STATIC_JS.glob("*.js")
        for match in CLIENT_TRANSLATION_RE.finditer(script_path.read_text(encoding="utf-8"))
    }

    assert sorted(messages - set(CLIENT_TRANSLATION_MESSAGES)) == []


def test_base_navigation_uses_endpoint_links_and_active_state():
    """Verify shell navigation uses Flask endpoints instead of raw path checks."""
    base_template = (TEMPLATES / "base.html").read_text(encoding="utf-8")

    assert "request.path" not in base_template
    assert 'href="/' not in base_template
    for endpoint in [
        "home.home",
        "auth.account",
        "dashboard.dashboard",
        "comparison.comparison",
        "calendar_page.calendar_view",
        "recurring.recurring",
        "upload.upload",
        "transactions.transactions",
        "review.review",
        "rules.rules",
        "taxonomy_admin.taxonomy",
        "jobs.jobs",
        "settings_page.settings_page",
        "auth.users",
    ]:
        assert f"url_for('{endpoint}')" in base_template
