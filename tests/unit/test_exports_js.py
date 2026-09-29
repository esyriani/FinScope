"""Static architecture checks for shared browser export helpers.

Detailed export behavior is exercised by the Vitest/jsdom suite. These tests
keep the architecture and asset-loading boundaries visible from the Python suite.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORTS_JS = PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js"
XLSX_WRITER_JS = PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "xlsx-writer.js"
EXPORTS_CSS = PROJECT_ROOT / "src" / "finance_app" / "static" / "css" / "exports.css"
TEMPLATES = PROJECT_ROOT / "src" / "finance_app" / "templates"


def test_export_toolbars_include_expand_modal_actions():
    """Verify table and chart export toolbars expose reusable expand controls."""
    source = EXPORTS_JS.read_text(encoding="utf-8")
    styles = EXPORTS_CSS.read_text(encoding="utf-8")

    assert "function createExpandButton" in source
    assert "export-button-icon-only" in source
    assert "ensureExportExpandModal" in source
    assert "window.financeApp.closeExpandedExportModal = closeExpandedExportModal;" in source
    assert "window.financeApp.showModalAfterExpandedExportCloses = showModalAfterExpandedExportCloses;" in source
    assert ".export-expand-modal .modal-body" in styles
    assert ".export-button-icon-only" in styles
    assert ".export-expanded-chart.chart-viewport" in styles
    assert ".export-expanded-table" in styles
    assert "min-width: max-content;" not in styles


def test_xlsx_file_format_writer_is_isolated_from_dom_table_exports():
    """Verify XLSX packaging logic lives outside the generic DOM export script."""
    source = EXPORTS_JS.read_text(encoding="utf-8")
    writer = XLSX_WRITER_JS.read_text(encoding="utf-8")

    assert "function buildTableExportWorkbookSource" in source
    assert "function createTableExportXlsxBlob" in source
    assert "writer.createXlsxBlob(source, sheetName)" in source
    assert "window.financeXlsxWriter =" in writer
    assert "createXlsxBlob" in writer
    assert "createXlsxBlob" in source

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
