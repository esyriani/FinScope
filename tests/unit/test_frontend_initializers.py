"""Static regression checks for frontend initializer wiring."""

import re
from pathlib import Path

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


def test_busy_overlay_ignores_prevented_submits():
    """Verify custom AJAX submit handlers do not leave navigation overlay tokens open."""
    busy_overlay = read_script("busy-overlay.js")

    submit_listener = busy_overlay.split('document.addEventListener("submit"', 1)[1]

    assert "event.defaultPrevented" in submit_listener.split("showBusyOverlayForElement", 1)[0]


def test_upload_preview_shows_busy_overlay_while_loading():
    """Verify statement preview parsing gives immediate busy feedback."""
    upload_js = read_script("upload.js")

    submit_listener = upload_js.split('form.addEventListener("submit", async (event) => {', 1)[1]
    preview_fetch = submit_listener.split("const response = await fetch(previewUrl", 1)[0]
    preview_finally = submit_listener.split("} finally {", 1)[1]

    assert "window.showBusyOverlay?.({" in preview_fetch
    assert 'message: translate("Preparing statement preview...")' in preview_fetch
    assert "window.hideBusyOverlay?.(previewBusyToken);" in preview_finally.split("modal?.show()", 1)[0]


def test_upload_file_picker_feedback_paints_before_native_selector():
    """Verify file selection shows busy feedback before the native picker opens."""
    upload_template = (TEMPLATES / "upload.html").read_text(encoding="utf-8")
    upload_js = read_script("upload.js")
    busy_overlay = read_script("busy-overlay.js")

    file_picker_setup = upload_js.split("function setupUploadFileSelectionFeedback", 1)[1].split(
        "function setupUploadPreview", 1
    )[0]
    open_helper = file_picker_setup.split("const openFilePickerAfterOverlayPaint", 1)[1].split(
        'browseButton.addEventListener("click"', 1
    )[0]
    assert "data-upload-file-input" in upload_template
    assert "data-upload-file-browse" in upload_template
    assert "data-upload-file-name" in upload_template
    assert "options.immediate === true" in busy_overlay
    assert (
        "renderBusyOverlay(token);"
        in busy_overlay.split("options.immediate === true", 1)[1].split("busyOverlayState.showTimer", 1)[0]
    )
    assert 'message: translate("Opening statement...")' in file_picker_setup
    assert "immediate: true" in file_picker_setup
    assert "fileInput.click();" in open_helper
    assert "window.requestAnimationFrame(() =>" in open_helper
    assert "window.requestAnimationFrame(openPicker);" in open_helper
    assert (
        "openFilePickerAfterOverlayPaint();" in file_picker_setup.split('browseButton.addEventListener("click"', 1)[1]
    )
    assert "fileNameNode.textContent = label;" in file_picker_setup
    assert "window.hideBusyOverlay?.(selectionBusyToken);" in file_picker_setup


def test_transaction_action_dropdowns_escape_table_scroll_clipping():
    """Verify row action menus can render outside short responsive table scrollers."""
    transactions_template = (TEMPLATES / "transactions.html").read_text(encoding="utf-8")
    action_menu = transactions_template.split('class="dropdown transaction-action-menu"', 1)[1].split(
        "aria-label=\"{{ _('More actions') }}\"", 1
    )[0]

    assert 'data-bs-toggle="dropdown"' in action_menu
    assert 'data-bs-boundary="viewport"' in action_menu
    assert """data-bs-popper-config='{"strategy":"fixed"}'""" in action_menu


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
            line_number = template[: match.start()].count("\n") + 1
            offenders.append(f"{template_path.relative_to(ROOT)}:{line_number}")

    assert offenders == []


def test_interactive_table_rows_have_keyboard_semantics():
    """Verify clickable table rows expose focus and keyboard activation behavior."""
    tables_js = read_script("tables.js")

    assert "row.tabIndex = 0" in tables_js
    assert 'row.setAttribute("role", "button")' in tables_js
    assert 'row.addEventListener("keydown"' in tables_js
    assert 'event.key !== "Enter" && event.key !== " "' in tables_js
    assert "window.financeApp?.showModalAfterExpandedExportCloses" in tables_js


def test_shared_tables_support_client_quick_search():
    """Verify sortable and paginated tables share client-side quick search behavior."""
    tables_js = read_script("tables.js")

    assert "function setupTableSearch" in tables_js
    assert '"[data-table-search]"' in tables_js
    assert "data-table-search-target" in (TEMPLATES / "reports.html").read_text(encoding="utf-8")
    assert 'table.dispatchEvent(new CustomEvent("finance:table-filtered"))' in tables_js
    assert 'table.addEventListener("finance:table-filtered"' in tables_js
    assert 'registerInitializer("tables.search"' in tables_js


def test_flatpickr_initializers_use_document_and_cleanup_instances():
    """Verify date controls initialize on DOM ready and are cleaned before AJAX swaps."""
    dates_js = read_script("dates.js")
    recurring_js = read_script("recurring.js")

    assert 'document.addEventListener("DOMContentLoaded", () => setupFlatpickrInputs())' in dates_js
    assert "input.financeFlatpickr = flatpickr(input" in dates_js
    assert "destroyDynamicFlatpickr(currentDynamic)" in recurring_js
    assert "window.financeApp?.showModalAfterExpandedExportCloses" in recurring_js


def test_recurring_table_actions_use_batch_and_row_handlers():
    """Verify recurring list actions are wired without row double-click conflicts."""
    recurring_js = read_script("recurring.js")

    assert "function applyRecurringAction(id, action)" in recurring_js
    assert "function setupRecurringBatchActions()" in recurring_js
    assert "function recurringPatternItems(item)" in recurring_js
    assert "dataset.recurringPatternKey" in recurring_js
    assert '"[data-recurring-batch-table]"' in recurring_js
    assert '"[data-recurring-row-confirm]"' in recurring_js
    assert '"[data-recurring-row-remove]"' in recurring_js
    assert '"[data-recurring-row-edit]"' in recurring_js
    assert "event.stopPropagation()" in recurring_js
    assert "event.target.closest(interactiveSelector)" in recurring_js


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


def test_reports_layout_uses_wide_monthly_charts_and_collapsed_tables():
    """Verify report charts and table panels keep the shared responsive layout."""
    reports_template = (TEMPLATES / "reports.html").read_text(encoding="utf-8")
    reports_css = read_style("reports.css")
    exports_js = read_script("exports.js")

    assert "data-collapse-panel-header-toggle" in reports_template
    assert "data-collapse-panel-heading-toggle" in reports_template
    assert "data-table-export-toolbar" in reports_template
    assert '"[data-table-export-toolbar]"' in exports_js
    assert "reports-table-toggle" not in reports_template
    assert 'class="collapse reports-table-collapse" id="{{ panel_id }}"' in reports_template
    assert 'reports_chart_card("Monthly statement", "reportsMonthlyChart", true)' in reports_template
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in reports_css
    assert ".reports-chart-card-wide" in reports_css
    assert "grid-column: 1 / -1;" in reports_css
    table_grid_rules = re.findall(r"\.reports-table-grid\s*\{(?P<body>[^}]*)\}", reports_css)
    assert any("grid-template-columns: minmax(0, 1fr);" in rule for rule in table_grid_rules)


def test_comparison_monthly_table_view_switches_export_toolbars():
    """Verify the monthly yearly comparison can switch from chart PNG export to table export."""
    comparison_template = (TEMPLATES / "comparison.html").read_text(encoding="utf-8")
    comparison_js = read_script("comparison-charts.js")
    comparison_css = read_style("comparison.css")
    exports_js = read_script("exports.js")

    assert 'id="comparison_chart_table" value="table"' in comparison_template
    assert "data-comparison-monthly-visualization" in comparison_template
    assert 'id="comparisonMonthlyByYearTable"' in comparison_template
    assert "monthly_spending_comparison" in comparison_template
    assert "chartElement.hidden = tableSelected" in comparison_js
    assert "tableElement.hidden = !tableSelected" in comparison_js
    assert "visualization.dataset.comparisonMonthlyView = selectedView" in comparison_js
    assert 'window.dispatchEvent(new CustomEvent("finance:layoutchange"))' in comparison_js
    assert "function scheduleComparisonChartPaints()" in comparison_js
    assert "comparisonChartUtils.forceResize(chart)" in comparison_js
    assert 'window.addEventListener("finance:layoutchange", scheduleComparisonChartPaints)' in comparison_js
    assert "queueInitialComparisonChartRender()" in comparison_js
    assert "animation: false" in comparison_js
    assert 'toolbar.classList.add("table-export-toolbar")' in exports_js
    assert 'toolbar.classList.add("chart-export-toolbar")' in exports_js
    assert (
        '[data-comparison-monthly-visualization]:not([data-comparison-monthly-view="table"]) .table-export-toolbar'
        in comparison_css
    )
    assert (
        '[data-comparison-monthly-visualization][data-comparison-monthly-view="table"] .chart-export-toolbar'
        in comparison_css
    )


def test_tag_multiselect_summarizes_preset_selection():
    """Verify preset category selections render as one compact summary tag."""
    tag_multiselect_js = read_script("tag-multiselect.js")

    assert "selectPresetSummaryLabel" in tag_multiselect_js
    assert "selectionMatchesPreset(multiselect)" in tag_multiselect_js
    assert "renderedTag(presetSummaryLabel" in tag_multiselect_js
    assert "setPresetOptions(multiselect, false)" in tag_multiselect_js


def test_scrollable_modals_fit_content_height():
    """Verify scrollable Bootstrap modals do not stretch to full-page height."""
    base_css = read_style("base.css")

    assert ".modal-dialog-scrollable" in base_css
    assert "height: auto;" in base_css.split(".modal-dialog-scrollable", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in base_css.split(".modal-body", 1)[1].split("}", 1)[0]


def test_filter_panels_use_shared_collapsible_summary_macros():
    """Verify page filters collapse by default and reuse the shared toggle markup."""
    collapsible_template = (TEMPLATES / "_collapsible.html").read_text(encoding="utf-8")
    tables_js = read_script("tables.js")
    tables_css = read_style("tables.css")

    assert "macro collapsible_filter_panel" in collapsible_template
    assert "macro collapsible_filter_block" in collapsible_template
    assert "data-collapse-label-toggle" in collapsible_template
    assert "data-filter-panel-header-toggle" in collapsible_template
    assert "data-filter-panel-heading-toggle" in collapsible_template
    assert 'role="button"' in collapsible_template
    assert 'tabindex="0"' in collapsible_template
    assert '"Show filters", "Hide filters"' in collapsible_template
    assert 'class="collapse{% if expanded %} show{% endif %}"' in collapsible_template
    assert "filter-panel-summary" in collapsible_template
    assert "function setupFilterPanelHeaderToggles" in tables_js
    assert "collapsePanelHeaderInteractiveSelector" in tables_js
    assert "data-collapse-panel-header-toggle" in tables_js
    assert "data-collapse-panel-heading-toggle" in tables_js
    assert "function setupCollapsePanelStateSync" in tables_js
    assert "toggleFilterPanelTarget(target)" in tables_js
    assert 'event.key !== "Enter" && event.key !== " "' in tables_js
    assert 'registerInitializer("tables.filter-panel-header-toggles"' in tables_js
    assert "setFilterPanelHeadingExpanded(target, expanded)" in tables_js
    assert ".filter-panel-summary" in tables_css
    assert ".filter-panel-header[data-filter-panel-header-toggle]" in tables_css
    assert ".collapse-panel-header[data-collapse-panel-header-toggle]" in tables_css
    assert '.filter-panel-heading[role="button"]:focus-visible' in tables_css
    assert '.collapse-panel-heading[role="button"]:focus-visible' in tables_css

    panel_templates = [
        "dashboard.html",
        "transactions.html",
        "rules.html",
        "review.html",
        "calendar.html",
        "comparison.html",
        "reports.html",
        "recurring.html",
        "rules_audit.html",
    ]

    for template_name in panel_templates:
        template = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert "collapsible_filter_panel" in template


def test_dashboard_wide_cards_span_medium_width_grid():
    """Verify wide dashboard panels keep spanning both columns below desktop widths."""
    dashboard_template = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
    home_dashboard_css = read_style("home-dashboard.css")
    responsive_css = read_style("responsive.css")
    medium_breakpoint = responsive_css.split("@media (max-width: 1100px)", 1)[1].split(
        "@media",
        1,
    )[0]
    medium_dashboard_wide_rule = re.search(
        r"\.dashboard-wide\s*\{(?P<body>[^}]*)\}",
        medium_breakpoint,
    )

    assert '<section class="card dashboard-wide dashboard-report-hub mb-4">' in dashboard_template
    assert (
        "grid-column: 1 / -1;"
        in home_dashboard_css.split(".dashboard-wide", 1)[1].split(
            "}",
            1,
        )[0]
    )
    assert not medium_dashboard_wide_rule or "grid-column: auto;" not in medium_dashboard_wide_rule.group("body")


def test_dashboard_quick_view_buttons_are_radio_style_apply_filters():
    """Verify quick-view controls are browser-native radios applied by the form."""
    dashboard_template = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
    dashboard_js = read_script("dashboard.js")
    base_css = read_style("base.css")
    home_dashboard_css = read_style("home-dashboard.css")

    quick_view_section = dashboard_template.split("aria-label=\"{{ _('Quick view') }}\"", 1)[1].split(
        "{% endfor %}",
        1,
    )[0]

    assert "app-toggle-group" in dashboard_template
    assert 'role="radiogroup"' in dashboard_template
    assert 'class="btn-check"' in quick_view_section
    assert 'type="radio"' in quick_view_section
    assert 'name="quick_view"' in quick_view_section
    assert 'value="{{ option.value }}"' in quick_view_section
    assert "app-toggle-option btn btn-sm btn-outline-secondary text-nowrap" in quick_view_section
    assert 'type="hidden" name="quick_view"' not in dashboard_template
    assert "data-dashboard-quick-view" not in dashboard_template
    assert "data-dashboard-quick-view-submit" not in dashboard_template
    assert "setupDashboardQuickView" not in dashboard_js
    assert "event.submitter" not in dashboard_js
    assert ".btn-check:checked + .app-toggle-option" in base_css
    assert "box-shadow: 0 0 0 2px rgba(var(--app-accent-rgb), 0.32);" in base_css
    assert "dashboard-quick-view" not in home_dashboard_css


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


def test_base_navigation_places_account_after_settings_before_users():
    """Verify account navigation stays in the admin sequence."""
    base_template = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    settings_index = base_template.index("url_for('settings_page.settings_page')")
    account_index = base_template.index("url_for('auth.account')")
    users_index = base_template.index("url_for('auth.users')")

    assert settings_index < account_index < users_index
