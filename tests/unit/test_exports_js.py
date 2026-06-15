"""Static regression tests for shared browser export helpers.

Guards the CSV export security boundary without requiring a JavaScript runtime
inside the Python test suite.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORTS_JS = PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js"
EXPORTS_CSS = PROJECT_ROOT / "src" / "finance_app" / "static" / "css" / "exports.css"


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
    assert "function showModalAfterExpandedExportCloses(modalElement)" in source
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
