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


def test_blob_download_urls_are_revoked_after_a_task_boundary():
    """Verify browser downloads can consume generated blob URLs before revocation."""
    source = EXPORTS_JS.read_text(encoding="utf-8")
    download_body = function_body(source, "downloadBlob")

    assert "const DOWNLOAD_URL_REVOKE_DELAY_MS = 1000;" in source
    assert "link.click();" in download_body
    assert "link.remove();" in download_body
    assert "window.setTimeout(() => URL.revokeObjectURL(url), DOWNLOAD_URL_REVOKE_DELAY_MS);" in download_body
    assert download_body.index("link.click();") < download_body.index("link.remove();")
    assert download_body.index("link.remove();") < download_body.index("window.setTimeout")


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


def test_table_exports_are_limited_to_displayed_dom_rows():
    """Verify generic table exports do not fetch or parse server pagination pages."""
    source = EXPORTS_JS.read_text(encoding="utf-8")
    rows_body = function_body(source, "tableRowsForExport")
    csv_body = function_body(source, "exportTableCsv")
    excel_body = function_body(source, "exportTableExcel")
    setup_body = function_body(source, "setupTableExports")

    assert "function tableRowsForExport(table)" in source
    assert "visibleExportSourceIds(table)" in rows_body
    assert "isDisplayedExportRow(row)" in rows_body
    assert "serverPaginationPlan" not in source
    assert "fetchExportTablePage" not in source
    assert "fetch(" not in source
    assert "DOMParser" not in source
    assert "tableExportTablesForScope" not in source
    assert "tableRowsForExportTables" not in source

    assert "const csv = tableMatrix(table)" in csv_body
    assert "buildTableExportWorkbookSource(table)" in excel_body
    assert 'financeTranslate("The table could not be exported.")' in source
    assert "event.currentTarget" in setup_body


def test_xlsx_file_format_writer_is_isolated_from_dom_table_exports():
    """Verify XLSX packaging logic lives outside the generic DOM export script."""
    source = EXPORTS_JS.read_text(encoding="utf-8")
    writer = XLSX_WRITER_JS.read_text(encoding="utf-8")
    excel_body = function_body(source, "exportTableExcel")

    assert "function buildTableExportWorkbookSource" in source
    assert "function createTableExportXlsxBlob" in source
    assert "buildTableExportWorkbookSource(table)" in excel_body
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

    assert "querySelector" in source
    assert "DOMParser" not in source
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
