"""Static regression tests for shared browser export helpers.

Guards the CSV export security boundary without requiring a JavaScript runtime
inside the Python test suite.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORTS_JS = PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js"
XLSX_WRITER_JS = PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "xlsx-writer.js"
EXPORTS_CSS = PROJECT_ROOT / "src" / "finance_app" / "static" / "css" / "exports.css"
TEMPLATES = PROJECT_ROOT / "src" / "finance_app" / "templates"


def function_body(source, name):
    """Return the body text of a top-level JavaScript function."""
    match = re.search(rf"function {re.escape(name)}\([^)]*\) {{", source)
    assert match is not None
    index = match.end()
    depth = 1
    while index < len(source) and depth:
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
        index += 1
    return source[match.end() : index - 1]


def test_csv_escape_neutralizes_spreadsheet_formula_prefixes():
    """Verify CSV exports sanitize values that spreadsheet apps treat as formulas."""
    source = EXPORTS_JS.read_text(encoding="utf-8")
    sanitizer_body = function_body(source, "sanitizeCsvFormulaValue")
    csv_escape_body = function_body(source, "csvEscape")

    assert "const CSV_FORMULA_PREFIX_RE = /^[=+\\-@\\t\\r]/;" in source
    assert 'String(value ?? "")' in sanitizer_body
    assert "CSV_FORMULA_PREFIX_RE.test(text) ? `'${text}` : text" in sanitizer_body
    assert "const text = sanitizeCsvFormulaValue(value);" in csv_escape_body
    assert 'String(value ?? "")' not in csv_escape_body


def test_export_toolbars_include_expand_modal_actions():
    """Verify table and chart export toolbars include reusable expand controls."""
    source = EXPORTS_JS.read_text(encoding="utf-8")
    styles = EXPORTS_CSS.read_text(encoding="utf-8")

    assert "function createExpandButton" in source
    assert "function createIconToolbarButton" in source
    assert "export-button-icon-only" in source
    assert 'financeTranslate("Expand")' in source
    assert 'financeTranslate("Expand {label}", { label: title })' in source
    assert "ensureExportExpandModal" in source
    assert "restoreExpandedExportContent" in source
    assert "function closeExpandedExportModal()" in source
    assert "function showModalAfterExpandedExportCloses(modalElement, relatedTarget)" in source
    assert "window.financeApp.closeExpandedExportModal = closeExpandedExportModal;" in source
    assert "window.financeApp.showModalAfterExpandedExportCloses = showModalAfterExpandedExportCloses;" in source
    assert "function tableVisibleSource(table)" in source
    assert "const sourceTable = tableVisibleSource(table) || table;" in source
    assert "const toolbarTable = tableVisibleSource(table) || table;" in source
    assert "bootstrap.Modal.getOrCreateInstance(modalElement).show();" in source
    assert "requestAnimationFrame(() => resizeChartElement(state.element));" in source
    assert "toolbar.appendChild(createExpandButton(title, () => expandTable(table, title)));" in source
    assert "toolbar.appendChild(createExpandButton(title, () => expandChart(container, title)));" in source
    assert "toolbar.appendChild(createExpandButton(title, () => expandChart(canvas, title)));" in source
    assert ".export-expand-modal .modal-body" in styles
    assert ".export-button-icon-only" in styles
    assert ".export-expanded-chart.chart-viewport" in styles
    assert ".export-expanded-table" in styles
    assert "min-width: max-content;" not in styles


def test_server_paginated_table_exports_fetch_pages_sequentially_with_cancellation():
    """Verify all-page table exports do not fetch every server page concurrently."""
    source = EXPORTS_JS.read_text(encoding="utf-8")
    fetch_body = function_body(source, "fetchExportTablePage")
    tables_body = function_body(source, "tableExportTablesForScope")
    csv_body = function_body(source, "exportTableCsv")
    excel_body = function_body(source, "exportTableExcel")
    setup_body = function_body(source, "setupTableExports")

    assert "const tableExportOperations = new WeakMap();" in source
    assert "function beginTableExportOperation(table, button)" in source
    assert "cancelTableExportOperation(tableExportOperations.get(table));" in source
    assert "function cancelTableExportFromButton(table, button)" in source
    assert "function updateTableExportProgress(operation, current, total)" in source
    assert 'financeTranslate("Cancel export ({current}/{total})", { current, total })' in source
    assert "function yieldTableExportWork()" in source

    assert "signal," in fetch_body
    assert "Promise.all" not in tables_body
    assert "for (const { pageNumber, url } of plan.pageUrls)" in tables_body
    assert "throwIfTableExportCancelled(options.operation);" in tables_body
    assert "options.onProgress?.({ currentPage, pageNumber, totalPages });" in tables_body
    assert "await fetchExportTablePage(url, table, sourceIndex, options.operation?.controller?.signal)" in tables_body
    assert "await yieldTableExportWork();" in tables_body

    assert "cancelTableExportFromButton(table, button)" in csv_body
    assert "cancelTableExportFromButton(table, button)" in excel_body
    assert "updateTableExportProgress(operation, currentPage, totalPages);" in csv_body
    assert "updateTableExportProgress(operation, currentPage, totalPages);" in excel_body
    assert "tableExportIsAbortError(error)" in csv_body
    assert "tableExportIsAbortError(error)" in excel_body
    assert "event.currentTarget" in setup_body


def test_xlsx_file_format_writer_is_isolated_from_dom_table_exports():
    """Verify XLSX packaging logic lives outside the generic DOM export script."""
    source = EXPORTS_JS.read_text(encoding="utf-8")
    writer = XLSX_WRITER_JS.read_text(encoding="utf-8")
    excel_body = function_body(source, "exportTableExcel")

    assert "function buildTableExportWorkbookSource" in source
    assert "function createTableExportXlsxBlob" in source
    assert "buildTableExportWorkbookSource(table, scope, exportTables)" in excel_body
    assert "writer.createXlsxBlob(source, sheetName)" in source

    for snippet in (
        "const XLSX_MIME_TYPE",
        "const XLSX_MONEY_FORMAT",
        "const XLSX_CRC32_TABLE",
        "function buildXlsxTableModel",
        "function createXlsxZipBlob",
        "function xlsxWorksheetXml",
        "function xlsxTableXml",
        "SUBTOTAL(109,",
        "window.financeXlsxWriter =",
    ):
        assert snippet in writer
        assert snippet not in source

    assert "DOMParser" in source
    assert "querySelector" in source
    assert "DOMParser" not in writer
    assert "querySelector" not in writer


def test_export_pages_load_xlsx_writer_before_table_exports():
    """Verify export-enabled templates load the XLSX writer before exports.js."""
    export_templates = [
        path for path in TEMPLATES.glob("*.html") if '"js/exports.js"' in path.read_text(encoding="utf-8")
    ]

    assert export_templates
    for template_path in export_templates:
        source = template_path.read_text(encoding="utf-8")
        assert '"js/xlsx-writer.js"' in source
        assert source.index('"js/xlsx-writer.js"') < source.index('"js/exports.js"')
