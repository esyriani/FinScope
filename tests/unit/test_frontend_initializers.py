"""Static regression checks for frontend initializer wiring."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC_JS = ROOT / "src" / "finance_app" / "static" / "js"
STATIC_CSS = ROOT / "src" / "finance_app" / "static" / "css"
TEMPLATES = ROOT / "src" / "finance_app" / "templates"
SCRIPT_TAG_RE = re.compile(r"<script\b(?P<attrs>[^>]*)>", re.IGNORECASE)
CLIENT_TRANSLATION_RE = re.compile(
    r"\b(?:"
    r"ajaxRefreshTranslate|busyOverlayTranslate|dashboardTranslate|financeChartTranslate|financeTranslate|"
    r"merchantAutocompleteTranslate|reportsTranslate|reviewTranslate|settingsTranslate|translate|"
    r"translateJobsMessage|translateTableMessage|uploadTranslate|t"
    r')\(\s*"([^"]+)"'
)


def read_script(name):
    """Return the JavaScript source for a static app script."""
    return (STATIC_JS / name).read_text(encoding="utf-8")


def read_style(name):
    """Return the CSS source for a static app stylesheet."""
    return (STATIC_CSS / name).read_text(encoding="utf-8")


def registered_client_translation_messages():
    """Return all browser-facing messages registered for client translation."""
    from finance_app.core.client_i18n import (
        client_translation_messages,
        register_core_client_translation_messages,
    )
    from finance_app.modules.client_i18n import register_module_client_translation_messages

    register_core_client_translation_messages()
    register_module_client_translation_messages()
    return set(client_translation_messages())


def test_ajax_refresh_uses_initializer_registry():
    """Verify AJAX refreshes use the shared registry instead of page-specific globals."""
    ajax_actions = read_script("ajax-actions.js")

    assert "window.financeApp?.runInitializers(root)" in ajax_actions
    assert "window.setupDashboardPage" not in ajax_actions
    assert "window.setupUploadPreview" not in ajax_actions
    assert "window.setupTableExports" not in ajax_actions


def test_ajax_refresh_get_requests_are_sequenced_by_target():
    """Verify shared AJAX GET refreshes abort and ignore superseded responses."""
    ajax_actions = read_script("ajax-actions.js")
    jobs_js = read_script("jobs.js")

    assert "const ajaxRefreshRequests = new Map();" in ajax_actions
    assert "let ajaxRefreshSequence = 0;" in ajax_actions
    assert "function beginAjaxRefreshRequest(selector, options = {})" in ajax_actions
    assert "previousRequest?.controller?.abort();" in ajax_actions
    assert "sequence: (ajaxRefreshSequence += 1)" in ajax_actions
    assert "function ajaxRefreshIsCurrentRequest(request)" in ajax_actions
    assert "return ajaxRefreshStaleResult(request);" in ajax_actions
    assert "signal: request.controller?.signal" in ajax_actions
    assert "await ajaxRefreshFromUrl(actionUrl, selector, { request });" in ajax_actions
    assert "await ajaxRefreshFromUrl(refreshUrl, selector, { request });" in ajax_actions
    assert "if (ajaxRefreshIsCurrentRequest(request)) {\n            showAjaxRefreshError" in ajax_actions
    assert 'link.dataset.ajaxRefreshInFlight === "true"' in ajax_actions
    assert 'link.dataset.ajaxRefreshInFlight = "true";' in ajax_actions
    assert "delete link.dataset.ajaxRefreshInFlight;" in ajax_actions

    assert "const refreshResult = await window.ajaxRefreshFromUrl(window.location.href, selector);" in jobs_js
    assert "if (refreshResult?.applied !== false)" in jobs_js


def test_dynamic_page_ajax_navigation_uses_shared_refresh_helper():
    """Verify dynamic page refreshes share fetch, sequencing, and replacement logic."""
    ajax_actions = read_script("ajax-actions.js")

    assert "function createAjaxDynamicPageRefresh(options = {})" in ajax_actions
    assert "function ajaxRefreshDynamicPage(url, options, replaceOptions = {})" in ajax_actions
    assert "const request = beginAjaxRefreshRequest(options.selector);" in ajax_actions
    assert "if (!ajaxRefreshIsCurrentRequest(request))" in ajax_actions
    assert "const nextTarget = nextDocument.querySelector(options.selector);" in ajax_actions
    assert "currentTarget.replaceWith(replacement);" in ajax_actions
    assert "runAjaxRefreshInitializers(replacement);" in ajax_actions
    assert "createDynamicPageRefresh: createAjaxDynamicPageRefresh" in ajax_actions

    for script_name, route_dataset_key, loading_class, history_key in [
        ("calendar.js", "calendarUrl", "calendar-dynamic-loading", "calendarAjax"),
        ("recurring.js", "recurringUrl", "recurring-dynamic-loading", "recurringAjax"),
    ]:
        script = read_script(script_name)

        assert "window.financeApp.createDynamicPageRefresh({" in script
        assert f'routeDatasetKey: "{route_dataset_key}"' in script
        assert f'loadingClass: "{loading_class}"' in script
        assert f"historyState: {{ {history_key}: true }}" in script
        assert "beforeReplace: ({ currentTarget }) =>" in script
        assert "refresh.replace(url" in script
        assert "let dynamicRefreshRequest = null;" not in script
        assert "let dynamicRefreshSequence = 0;" not in script
        assert "function dynamicRefreshIsCurrent(request)" not in script
        assert "new DOMParser()" not in script


def test_calendar_recurring_and_rules_routes_are_template_supplied():
    """Verify browser route guards and actions use server-rendered URLs."""
    calendar_js = read_script("calendar.js")
    recurring_js = read_script("recurring.js")
    rules_js = read_script("rules.js")
    calendar_template = (TEMPLATES / "calendar.html").read_text(encoding="utf-8")
    recurring_template = (TEMPLATES / "recurring.html").read_text(encoding="utf-8")
    rules_template = (TEMPLATES / "rules.html").read_text(encoding="utf-8")

    assert "data-calendar-url=\"{{ url_for('calendar_page.calendar_view') }}\"" in calendar_template
    assert 'routeDatasetKey: "calendarUrl"' in calendar_js
    assert 'url.pathname === "/calendar"' not in calendar_js

    assert "data-recurring-url=\"{{ url_for('recurring.recurring') }}\"" in recurring_template
    assert "data-recurring-confirm-url=\"{{ url_for('recurring.confirm_recurring_pattern') }}\"" in recurring_template
    assert "data-recurring-ignore-url=\"{{ url_for('recurring.ignore_recurring_pattern') }}\"" in recurring_template
    assert "data-recurring-edit-url=\"{{ url_for('recurring.edit_recurring_pattern') }}\"" in recurring_template
    assert 'routeDatasetKey: "recurringUrl"' in recurring_js
    assert "function recurringActionUrl(action)" in recurring_js
    assert "routes.recurringConfirmUrl" in recurring_js
    assert "routes.recurringIgnoreUrl" in recurring_js
    assert "routes.recurringEditUrl" in recurring_js
    assert '"/recurring/patterns/confirm"' not in recurring_js
    assert '"/recurring/patterns/ignore"' not in recurring_js
    assert '"/recurring/patterns/edit"' not in recurring_js
    assert 'url.pathname === "/recurring"' not in recurring_js

    assert "data-rule-preview-url=\"{{ url_for('rules.preview_rule') }}\"" in rules_template
    assert 'data-rule-preview-url="/rules/preview"' not in rules_template
    assert 'const url = form.getAttribute("data-rule-preview-url");' in rules_js


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


def test_reinitialized_controls_use_guarded_global_listeners():
    """Verify AJAX-replaced controls do not add one global listener per instance."""
    merchant_js = read_script("merchant-autocomplete.js")
    reports_js = read_script("reports.js")
    upload_js = read_script("upload.js")

    merchant_setup = merchant_js.split("function setupMerchantAutocomplete(root = document)", 1)[1].split(
        "window.financeApp?.registerInitializer", 1
    )[0]
    reports_open_setup = reports_js.split("function setupReportsOpenControls", 1)[1].split(
        "function setupReportsTargetSwitchers", 1
    )[0]
    upload_file_setup = upload_js.split("function setupUploadFileSelectionFeedback", 1)[1].split(
        "function setupUploadPreview", 1
    )[0]

    assert 'window.financeMerchantAutocompleteGlobalReady === "true"' in merchant_js
    assert merchant_js.count('document.addEventListener("click"') == 1
    assert "control.addEventListener(merchantAutocompleteCloseEvent, clearMenu);" in merchant_setup
    assert 'document.addEventListener("click"' not in merchant_setup

    assert 'window.financeReportsOpenControlsGlobalReady === "true"' in reports_js
    assert reports_js.count('document.addEventListener("click"') == 1
    assert "document.querySelectorAll(reportsOpenControlSelector)" in reports_js
    assert "control.addEventListener(reportsOpenControlCloseEvent, clearMenu);" in reports_open_setup
    assert 'document.addEventListener("click"' not in reports_open_setup

    assert 'window.financeUploadFileSelectionGlobalReady === "true"' in upload_js
    assert upload_js.count('window.addEventListener("focus"') == 1
    assert "document.querySelectorAll(\"[data-upload-form][data-upload-file-selection-ready='true']\")" in upload_js
    assert "form.addEventListener(uploadFileSelectionWindowFocusEvent" in upload_file_setup
    assert 'window.addEventListener("focus"' not in upload_file_setup


def test_autocomplete_listboxes_publish_active_descendant():
    """Verify combobox inputs expose the visually active listbox option."""
    merchant_js = read_script("merchant-autocomplete.js")
    reports_js = read_script("reports.js")
    filter_controls_template = (TEMPLATES / "_filter_controls.html").read_text(encoding="utf-8")
    reports_explorers_template = (TEMPLATES / "_reports_explorers.html").read_text(encoding="utf-8")

    assert 'role="combobox"' in filter_controls_template
    assert 'aria-autocomplete="list"' in filter_controls_template
    assert 'aria-controls="{{ autocomplete_menu_id }}"' in filter_controls_template
    assert 'role="listbox" hidden data-merchant-autocomplete-menu' in filter_controls_template

    assert 'aria-controls="reports-taxonomy-open-suggestions"' in reports_explorers_template
    assert 'aria-controls="{{ control_id }}-suggestions"' in reports_explorers_template
    assert 'role="listbox" hidden data-taxonomy-open-menu' in reports_explorers_template
    assert 'role="listbox" hidden data-report-open-menu' in reports_explorers_template

    assert "function merchantAutocompleteOptionId(index)" in merchant_js
    assert "option.id = merchantAutocompleteOptionId(index);" in merchant_js
    assert "option.tabIndex = -1;" in merchant_js
    assert 'input.setAttribute("aria-activedescendant", activeOption.id);' in merchant_js
    assert 'input.removeAttribute("aria-activedescendant");' in merchant_js
    assert (
        "setActiveDescendant(null);"
        in merchant_js.split("function setExpanded", 1)[1].split("function clearMenu", 1)[0]
    )

    assert "function reportsOpenOptionId(index)" in reports_js
    assert "option.id = reportsOpenOptionId(index);" in reports_js
    assert "option.tabIndex = -1;" in reports_js
    assert 'input.setAttribute("aria-activedescendant", activeOption.id);' in reports_js
    assert 'input.removeAttribute("aria-activedescendant");' in reports_js
    assert (
        "setActiveDescendant(null);" in reports_js.split("function setExpanded", 1)[1].split("function clearMenu", 1)[0]
    )


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
    reports_tables_template = (TEMPLATES / "_reports_tables.html").read_text(encoding="utf-8")
    table_primitives_template = (TEMPLATES / "_table_primitives.html").read_text(encoding="utf-8")

    assert "function setupTableSearch" in tables_js
    assert '"[data-table-search]"' in tables_js
    assert "data-table-search-target" in table_primitives_template
    assert "client_table_search" in reports_tables_template
    assert 'table.dispatchEvent(new CustomEvent("finance:table-filtered"))' in tables_js
    assert 'table.addEventListener("finance:table-filtered"' in tables_js
    assert 'registerInitializer("tables.search"' in tables_js


def test_table_chrome_uses_shared_template_primitives():
    """Verify repeated table chrome is centralized without hiding search behavior."""
    table_primitives_template = (TEMPLATES / "_table_primitives.html").read_text(encoding="utf-8")
    reimbursements_template = (TEMPLATES / "reimbursements.html").read_text(encoding="utf-8")
    reports_tables_template = (TEMPLATES / "_reports_tables.html").read_text(encoding="utf-8")
    reports_explorers_template = (TEMPLATES / "_reports_explorers.html").read_text(encoding="utf-8")
    rules_audit_template = (TEMPLATES / "rules_audit.html").read_text(encoding="utf-8")
    rules_overlap_template = (TEMPLATES / "rules_audit_overlap.html").read_text(encoding="utf-8")
    rules_detail_template = (TEMPLATES / "rules_audit_rule.html").read_text(encoding="utf-8")

    for macro_name in (
        "client_table_search",
        "server_table_search",
        "export_toolbar",
        "table_card_header",
        "collapsible_table_header",
        "table_result_status",
        "pagination_footer",
        "empty_state",
        "empty_table_row",
    ):
        assert f"macro {macro_name}" in table_primitives_template

    assert "server_table_search" in reimbursements_template
    assert "empty_table_row" in reimbursements_template
    assert "macro table_search" not in reimbursements_template
    assert "macro table_card_header" not in reimbursements_template

    assert "client_table_search" in reports_tables_template
    assert "collapsible_table_header" in reports_tables_template
    assert "export_toolbar" in reports_tables_template
    assert "macro reports_table_search" not in reports_tables_template

    assert "table_card_header" in reports_explorers_template
    assert "empty_state" in reports_explorers_template
    assert "pagination_footer" in rules_audit_template
    assert "collapsible_table_header" in rules_audit_template
    assert "table_result_status" in rules_audit_template
    assert "table_card_header" in rules_overlap_template
    assert "pagination_footer" in rules_overlap_template
    assert "empty_state" in rules_detail_template
    assert rules_overlap_template.count("macro preview_delete_button") == 1


def test_flatpickr_initializers_use_document_and_cleanup_instances():
    """Verify date controls initialize on DOM ready and are cleaned before AJAX swaps."""
    dates_js = read_script("dates.js")
    recurring_js = read_script("recurring.js")

    assert 'document.addEventListener("DOMContentLoaded", () => setupFlatpickrInputs())' in dates_js
    assert "input.financeFlatpickr = flatpickr(input" in dates_js
    assert "destroyDynamicFlatpickr(currentTarget)" in recurring_js
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
    analytics_css = read_style("analytics-components.css")
    comparison_css = read_style("comparison.css")

    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in analytics_css
    assert "@media (max-width: 1100px)" in analytics_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in analytics_css
    assert "grid-template-columns: 1fr;" in analytics_css
    assert ".insight-card" not in comparison_css


def test_reports_layout_uses_wide_monthly_charts_and_collapsed_tables():
    """Verify report charts and table panels keep the shared responsive layout."""
    reports_template = (TEMPLATES / "reports.html").read_text(encoding="utf-8")
    reports_overview_template = (TEMPLATES / "_reports_section_overview.html").read_text(encoding="utf-8")
    reports_tables_template = (TEMPLATES / "_reports_tables.html").read_text(encoding="utf-8")
    table_primitives_template = (TEMPLATES / "_table_primitives.html").read_text(encoding="utf-8")
    reports_css = read_style("reports.css")
    exports_js = read_script("exports.js")

    assert "data-collapse-panel-header-toggle" in table_primitives_template
    assert "data-collapse-panel-heading-toggle" in table_primitives_template
    assert "data-table-export-toolbar" in table_primitives_template
    assert "collapsible_table_header" in reports_tables_template
    assert "export_toolbar" in reports_tables_template
    assert '"[data-table-export-toolbar]"' in exports_js
    assert "reports-table-toggle" not in reports_template
    assert "reports-table-toggle" not in reports_tables_template
    assert 'class="collapse reports-table-collapse" id="{{ panel_id }}"' in reports_tables_template
    assert 'reports_chart_card("Monthly statement", "reportsMonthlyChart", true)' in reports_overview_template
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in reports_css
    assert ".reports-chart-card-wide" in reports_css
    assert "grid-column: 1 / -1;" in reports_css
    table_grid_rules = re.findall(r"\.reports-table-grid\s*\{(?P<body>[^}]*)\}", reports_css)
    assert any("grid-template-columns: minmax(0, 1fr);" in rule for rule in table_grid_rules)


def test_reports_template_imports_focused_component_partials():
    """Verify the reports page delegates reusable report markup to partials."""
    reports_template = (TEMPLATES / "reports.html").read_text(encoding="utf-8")

    assert "{% macro" not in reports_template
    assert '{% include "_reports_section_overview.html" %}' in reports_template
    assert '{% include "_reports_section_taxonomy_detail.html" %}' in reports_template
    assert '{% include "_reports_section_taxonomy.html" %}' in reports_template
    assert '{% include "_reports_section_entity_detail.html" %}' in reports_template
    assert '{% include "_reports_section_entity.html" %}' in reports_template
    assert '{% include "_reports_section_income.html" %}' in reports_template

    component_expectations = {
        "_reports_filters.html": ("macro reports_filter_form",),
        "_reports_tables.html": ("macro reports_table", "macro evidence_table"),
        "_reports_summary_cards.html": (
            "macro reports_actions",
            "macro reports_chart_card",
            "macro reports_quality_panel",
        ),
        "_reports_explorers.html": (
            "macro taxonomy_explorer",
            "macro report_explorer",
            "macro taxonomy_detail_header",
            "macro entity_detail_header",
        ),
        "_reports_section_overview.html": (
            'from "_reports_filters.html" import reports_filter_form with context',
            'from "_reports_tables.html" import reports_table with context',
        ),
        "_reports_section_income.html": (
            "reports_income_key_insights",
            "evidence_table",
        ),
    }
    for template_name, snippets in component_expectations.items():
        template_source = (TEMPLATES / template_name).read_text(encoding="utf-8")
        for snippet in snippets:
            assert snippet in template_source


def test_reports_open_controls_and_explorers_use_shared_helpers():
    """Verify taxonomy and report explorer widgets share one implementation path."""
    reports_js = read_script("reports.js")

    assert "const taxonomyExplorerConfig = {" in reports_js
    assert "const reportExplorerConfig = {" in reports_js
    assert "function setupReportsOpenControls(root, config)" in reports_js
    assert "function setupReportsTargetSwitchers(root, config)" in reports_js
    assert "function setupReportsExplorer(root, config)" in reports_js
    assert "currentReportsExplorerState(root, config, control)" in reports_js
    assert "config.rowMatchesFilter(row, state.filter)" in reports_js
    assert "setupReportsOpenControls(root, taxonomyExplorerConfig);" in reports_js
    assert "setupReportsOpenControls(root, reportExplorerConfig);" in reports_js
    assert "setupReportsExplorer(root, taxonomyExplorerConfig);" in reports_js
    assert "setupReportsExplorer(root, reportExplorerConfig);" in reports_js
    assert "function setupTaxonomyOpenControls" not in reports_js
    assert "function setupReportOpenControls" not in reports_js
    assert "function setupTaxonomyExplorer" not in reports_js
    assert "function setupReportExplorers" not in reports_js
    assert reports_js.count('input.addEventListener("keydown"') == 1
    assert reports_js.count("merchant-autocomplete-option reports-taxonomy-open-option") == 1
    assert reports_js.count('switcher.addEventListener("shown.bs.dropdown"') == 1
    assert reports_js.count('table.dispatchEvent(new CustomEvent("finance:table-filtered"))') == 1


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


def test_tag_multiselect_uses_shared_accessible_disclosure_contract():
    """Verify tag multiselect markup and scripts keep the shared accessibility contract."""
    tag_multiselect_template = (TEMPLATES / "_tag_multiselect.html").read_text(encoding="utf-8")
    tag_multiselect_js = read_script("tag-multiselect.js")
    tag_multiselect_css = read_style("tag-multiselect.css")

    assert "macro tag_multiselect" in tag_multiselect_template
    assert "data-tag-multiselect-control" in tag_multiselect_template
    assert 'type="button"' in tag_multiselect_template
    assert 'aria-labelledby="{{ label_id }} {{ label_id }}-summary"' in tag_multiselect_template
    assert 'aria-controls="{{ label_id }}-menu"' in tag_multiselect_template
    assert 'role="group"' in tag_multiselect_template
    assert 'tabindex="-1"' in tag_multiselect_template
    assert "hidden" in tag_multiselect_template
    assert 'role="button"' not in tag_multiselect_template
    assert "function moveMenuFocus" in tag_multiselect_js
    assert "function focusMenuOption" in tag_multiselect_js
    assert 'event.key === "ArrowDown"' in tag_multiselect_js
    assert 'event.key === "ArrowUp"' in tag_multiselect_js
    assert 'event.key === "Home"' in tag_multiselect_js
    assert 'event.key === "End"' in tag_multiselect_js
    assert 'hideMenu(multiselect, "toggle")' in tag_multiselect_js
    assert ".tag-multiselect-toggle" in tag_multiselect_css
    assert ".tag-multiselect-control:focus-within" in tag_multiselect_css

    for template_name in (
        "transactions.html",
        "_reports_filters.html",
        "comparison.html",
        "calendar.html",
        "recurring.html",
        "rules.html",
    ):
        template_source = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert 'from "_tag_multiselect.html" import tag_multiselect with context' in template_source
        assert "{% call tag_multiselect" in template_source


def test_filter_controls_use_shared_template_contract():
    """Verify common filter markup lives behind the shared filter-control macros."""
    filter_controls_template = (TEMPLATES / "_filter_controls.html").read_text(encoding="utf-8")

    for macro_name in (
        "filter_summary",
        "hidden_inputs",
        "filter_actions",
        "period_filter",
        "date_range_filters",
        "account_filter",
        "merchant_filter",
    ):
        assert f"macro {macro_name}" in filter_controls_template

    summary_templates = (
        "dashboard.html",
        "transactions.html",
        "rules.html",
        "calendar.html",
        "recurring.html",
    )
    for template_name in summary_templates:
        template_source = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert 'from "_filter_controls.html" import' in template_source
        assert "filter_summary(" in template_source
        assert "filter_actions(" in template_source

    reports_template = (TEMPLATES / "reports.html").read_text(encoding="utf-8")
    reports_filters_template = (TEMPLATES / "_reports_filters.html").read_text(encoding="utf-8")

    assert 'from "_filter_controls.html" import filter_summary with context' in reports_template
    assert "filter_summary(" in reports_template
    assert 'from "_filter_controls.html" import' in reports_filters_template
    assert "filter_actions(" in reports_filters_template

    for template_name in ("dashboard.html", "transactions.html"):
        template_source = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert "period_filter(" in template_source
        assert "date_range_filters(" in template_source
    assert "period_filter(" in reports_filters_template
    assert "date_range_filters(" in reports_filters_template

    for template_name in (
        "dashboard.html",
        "transactions.html",
        "comparison.html",
        "calendar.html",
        "recurring.html",
    ):
        template_source = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert "account_filter(" in template_source
    assert "account_filter(" in reports_filters_template

    for template_name in ("dashboard.html", "comparison.html", "calendar.html", "recurring.html"):
        template_source = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert "merchant_filter(" in template_source
        assert "data-merchant-autocomplete" not in template_source
    assert "merchant_filter(" in reports_filters_template
    assert "data-merchant-autocomplete" not in reports_filters_template


def test_filter_summaries_are_service_shaped_for_main_filter_pages():
    """Verify main pages do not reconstruct selected filter-summary labels in Jinja."""
    for template_name in (
        "calendar.html",
        "comparison.html",
        "dashboard.html",
        "recurring.html",
        "reports.html",
        "transactions.html",
    ):
        template_source = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert "namespace(" not in template_source
        assert "filter_summary([" not in template_source
        assert "_summary_items.append" not in template_source
        assert "period_summary(" not in template_source
        assert "selected_filter_values(" not in template_source
        assert "selected_merchant_label = selected_merchant_label" not in template_source
        assert "_merchant_filter_label = selected_merchant_label if selected_merchant_id" not in template_source


def test_calendar_day_modal_uses_native_button_triggers():
    """Verify Calendar day modal wiring is attached to real buttons."""
    calendar_template = (TEMPLATES / "calendar.html").read_text(encoding="utf-8")
    calendar_js = read_script("calendar.js")
    calendar_css = read_style("calendar-recurring.css")

    assert 'role="button"' not in calendar_template
    assert 'tabindex="0"' not in calendar_template
    assert "data-calendar-day-open" in calendar_template
    assert "Review {date} transactions" in calendar_template
    assert '"[data-calendar-day-open]"' in calendar_js
    assert 'button.addEventListener("click"' in calendar_js
    assert 'day.addEventListener("keydown"' not in calendar_js
    assert ".calendar-day-action:focus-visible" in calendar_css


def test_sortable_tables_publish_accessible_sort_state():
    """Verify sortable table headers expose and update assistive sort state."""
    sort_controls_template = (TEMPLATES / "_sort_controls.html").read_text(encoding="utf-8")
    tables_js = read_script("tables.js")
    comparison_js = read_script("comparison.js")

    assert "macro server_sort_header" in sort_controls_template
    assert "macro client_sort_header" in sort_controls_template
    assert "aria-sort" in sort_controls_template
    assert '"ascending"' in sort_controls_template
    assert '"descending"' in sort_controls_template

    for script in (tables_js, comparison_js):
        assert 'querySelectorAll("th[aria-sort]")' in script
        assert 'removeAttribute("aria-sort")' in script
        assert 'setAttribute("aria-sort", ariaSort)' in script

    for template_name in (
        "transactions.html",
        "rules.html",
        "review.html",
        "rules_audit.html",
        "rules_audit_overlap.html",
    ):
        template_source = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert "server_sort_header" in template_source

    for template_name in (
        "_recurring_activity.html",
        "taxonomy.html",
        "reimbursements.html",
        "_reports_tables.html",
        "rules_audit_preview.html",
        "rules_audit_rule.html",
        "rules_import_preview.html",
        "comparison.html",
    ):
        template_source = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert "client_sort_header" in template_source

    comparison_template = (TEMPLATES / "comparison.html").read_text(encoding="utf-8")
    assert 'client_sort_header(3, "Change", "number", "text-end", "desc")' in comparison_template
    assert 'client_sort_header(4, "Change", "number", "text-end", "desc")' in comparison_template

    for template_path in TEMPLATES.rglob("*.html"):
        if template_path.name == "_sort_controls.html":
            continue
        assert "sort-icon {{" not in template_path.read_text(encoding="utf-8")


def test_comparison_template_uses_presenter_tone_for_insight_cards():
    """Verify comparison insight cards do not infer tone from English labels."""
    comparison_template = (TEMPLATES / "comparison.html").read_text(encoding="utf-8")

    assert "insight.tone" in comparison_template
    assert "insight.label | lower" not in comparison_template
    assert "label_text" not in comparison_template
    assert "'new spending' in" not in comparison_template
    assert "'decrease' in" not in comparison_template
    assert "'increase' in" not in comparison_template


def test_scrollable_modals_fit_content_height():
    """Verify scrollable Bootstrap modals do not stretch to full-page height."""
    base_css = read_style("base.css")

    assert ".modal-dialog-scrollable" in base_css
    assert "height: auto;" in base_css.split(".modal-dialog-scrollable", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in base_css.split(".modal-body", 1)[1].split("}", 1)[0]


def test_filter_panels_use_shared_collapsible_summary_macros():
    """Verify page filters collapse by default and reuse the shared toggle markup."""
    collapsible_template = (TEMPLATES / "_collapsible.html").read_text(encoding="utf-8")
    base_css = read_style("base.css")
    core_js = read_script("core.js")
    tables_js = read_script("tables.js")

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
    assert "function setupCoreFilterPanelHeaderToggles" in core_js
    assert "financeCollapsePanelHeaderInteractiveSelector" in core_js
    assert "data-collapse-panel-header-toggle" in core_js
    assert "data-collapse-panel-heading-toggle" in core_js
    assert "function setupCoreCollapsePanelStateSync" in core_js
    assert "financeToggleCollapsePanelTarget(target)" in core_js
    assert 'event.key !== "Enter" && event.key !== " "' in core_js
    assert 'registerInitializer("core.filter-panel-header-toggles"' in core_js
    assert 'registerInitializer("core.collapse-toggle-labels"' in core_js
    assert "financeSetCollapsePanelHeadingExpanded(target, expanded)" in core_js
    assert 'button.setAttribute("aria-expanded", expanded ? "true" : "false")' in core_js
    assert "function setupFilterPanelHeaderToggles" not in tables_js
    assert "function setupCollapseToggleLabels" not in tables_js
    assert 'registerInitializer("tables.filter-panel-header-toggles"' not in tables_js
    assert 'registerInitializer("tables.collapse-toggle-labels"' not in tables_js
    assert ".filter-panel-summary" in base_css
    assert ".filter-panel-header[data-filter-panel-header-toggle]" in base_css
    assert ".collapse-panel-header[data-collapse-panel-header-toggle]" in base_css
    assert '.filter-panel-heading[role="button"]:focus-visible' in base_css
    assert '.collapse-panel-heading[role="button"]:focus-visible' in base_css

    panel_templates = [
        "dashboard.html",
        "transactions.html",
        "rules.html",
        "review.html",
        "calendar.html",
        "comparison.html",
        "_reports_section_entity.html",
        "_reports_section_entity_detail.html",
        "_reports_section_income.html",
        "_reports_section_overview.html",
        "_reports_section_taxonomy.html",
        "_reports_section_taxonomy_detail.html",
        "recurring.html",
        "rules_audit.html",
    ]

    for template_name in panel_templates:
        template = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert "collapsible_filter_panel" in template


def test_dashboard_explore_reports_uses_compact_action_bar():
    """Verify Dashboard report shortcuts are a compact action bar."""
    dashboard_template = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
    dashboard_css = read_style("dashboard.css")
    responsive_css = read_style("responsive.css")

    assert '<section class="dashboard-action-bar mb-4">' in dashboard_template
    assert "dashboard-report-card" not in dashboard_template
    assert ".dashboard-action-bar" in dashboard_css
    assert ".dashboard-action-bar" not in responsive_css
    assert "font-size: 1.4rem;" in dashboard_css.split(".readiness-chip strong", 1)[1].split("}", 1)[0]


def test_dashboard_classification_scope_buttons_are_radio_style_apply_filters():
    """Verify classification-scope controls are browser-native radios applied by the form."""
    dashboard_template = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
    dashboard_js = read_script("dashboard.js")
    base_css = read_style("base.css")
    dashboard_css = read_style("dashboard.css")

    quick_view_section = dashboard_template.split("aria-label=\"{{ _('Scope') }}\"", 1)[1].split(
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
    assert "dashboard-drilldown" not in dashboard_template
    assert "dblclick" not in dashboard_js
    assert "event.submitter" not in dashboard_js
    assert ".btn-check:checked + .app-toggle-option" in base_css
    assert "box-shadow: 0 0 0 2px rgba(var(--app-accent-rgb), 0.32);" in base_css
    assert "dashboard-quick-view" not in dashboard_css


def test_shared_analytics_css_owns_cross_page_primitives():
    """Verify shared dashboard/home/report primitives are not hidden in page styles."""
    analytics_css = read_style("analytics-components.css")
    home_dashboard_css = read_style("home-dashboard.css")
    dashboard_css = read_style("dashboard.css")
    comparison_css = read_style("comparison.css")
    responsive_css = read_style("responsive.css")

    for selector in (".metric-grid", ".metric-card", ".chart-card", ".insight-card", ".section-title"):
        assert selector in analytics_css
        assert selector not in home_dashboard_css

    assert ".quality-panel" in dashboard_css
    assert ".quality-panel" not in home_dashboard_css
    assert ".quality-panel" not in responsive_css
    assert ".dashboard-action-bar" not in responsive_css
    assert ".calendar-control-row" not in responsive_css
    assert ".rule-audit-search-row" not in responsive_css
    assert ".insight-card" not in comparison_css


def test_dynamic_user_rows_avoid_inner_html_builders():
    """Verify dynamic user/import values are rendered through DOM APIs."""
    dynamic_scripts = ["rules.js", "recurring.js", "jobs.js", "upload.js", "calendar.js", "reimbursements.js"]

    for script_name in dynamic_scripts:
        script = read_script(script_name)
        assert "innerHTML =" not in script
        assert ".replaceChildren(" in script


def test_row_level_modals_use_shared_dynamic_shells():
    """Verify high-volume row actions do not render one hidden modal per row."""
    taxonomy_template = (TEMPLATES / "taxonomy.html").read_text(encoding="utf-8")
    taxonomy_modals = (TEMPLATES / "_taxonomy_modals.html").read_text(encoding="utf-8")
    taxonomy_js = read_script("taxonomy.js")
    reimbursements_template = (TEMPLATES / "reimbursements.html").read_text(encoding="utf-8")
    reimbursement_modals = (TEMPLATES / "_reimbursement_modals.html").read_text(encoding="utf-8")
    reimbursements_js = read_script("reimbursements.js")

    assert '"js/taxonomy.js"' in taxonomy_template
    assert '{% include "_taxonomy_modals.html" %}' in taxonomy_template
    assert "data-taxonomy-item=" in taxonomy_template
    assert 'data-bs-target="#edit-category-modal"' in taxonomy_template
    assert 'data-bs-target="#view-category-modal"' in taxonomy_template
    assert 'data-bs-target="#delete-category-modal"' in taxonomy_template
    assert 'data-bs-target="#edit-tag-modal"' in taxonomy_template
    assert 'data-bs-target="#view-tag-modal"' in taxonomy_template
    assert 'data-bs-target="#delete-tag-modal"' in taxonomy_template
    for row_modal_pattern in (
        "edit-category-{{ category.id }}",
        "view-category-{{ category.id }}",
        "delete-category-{{ category.id }}",
        "edit-tag-{{ tag.id }}",
        "view-tag-{{ tag.id }}",
        "delete-tag-{{ tag.id }}",
    ):
        assert row_modal_pattern not in taxonomy_template
        assert row_modal_pattern not in taxonomy_modals
    assert 'id="edit-category-modal"' in taxonomy_modals
    assert 'id="view-category-modal"' in taxonomy_modals
    assert 'id="delete-category-modal"' in taxonomy_modals
    assert 'id="edit-tag-modal"' in taxonomy_modals
    assert 'id="view-tag-modal"' in taxonomy_modals
    assert 'id="delete-tag-modal"' in taxonomy_modals
    assert 'registerInitializer("taxonomy.modals"' in taxonomy_js
    assert "innerHTML" not in taxonomy_js

    assert '{% include "_reimbursement_modals.html" %}' in reimbursements_template
    assert 'data-row-edit-target="#reimbursement-match-modal"' in reimbursements_template
    assert 'data-row-edit-target="#reimbursement-expense-modal"' in reimbursements_template
    assert 'data-bs-target="#reimbursement-match-modal"' in reimbursements_template
    assert "reimbursement_match_modal_items" in reimbursement_modals
    assert "expense_detail_modal_items" in reimbursement_modals
    assert 'id="reimbursement-match-modal"' in reimbursement_modals
    assert 'id="reimbursement-expense-modal"' in reimbursement_modals
    for row_modal_pattern in (
        "match-reimbursement-{{ item.id }}-modal",
        "match-reimbursement-{{ row.id }}-modal",
        "reimbursement-expense-{{ row.id }}-modal",
    ):
        assert row_modal_pattern not in reimbursements_template
        assert row_modal_pattern not in reimbursement_modals
    assert 'registerInitializer("reimbursements.modals"' in reimbursements_js
    assert "innerHTML" not in reimbursements_js


def test_client_translation_messages_cover_static_js_strings():
    """Verify direct browser translation strings are exposed to client i18n."""
    messages = {
        match.group(1)
        for script_path in STATIC_JS.glob("*.js")
        for match in CLIENT_TRANSLATION_RE.finditer(script_path.read_text(encoding="utf-8"))
    }

    assert sorted(messages - registered_client_translation_messages()) == []


def test_french_catalog_covers_client_translation_messages():
    """Verify registered browser messages are translated for French UI."""
    catalog = json.loads((ROOT / "src" / "finance_app" / "translations" / "fr.json").read_text(encoding="utf-8"))

    assert sorted(registered_client_translation_messages() - set(catalog)) == []


def test_app_factory_uses_client_translation_registry():
    """Verify browser translation ownership stays out of the app factory."""
    app_factory = (ROOT / "src" / "finance_app" / "__init__.py").read_text(encoding="utf-8")

    assert "CLIENT_TRANSLATION_MESSAGES =" not in app_factory
    assert "client_translation_messages()" in app_factory


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
