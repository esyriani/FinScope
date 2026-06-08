"""Static regression tests for shared browser export helpers.

Guards the CSV export security boundary without requiring a JavaScript runtime
inside the Python test suite.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORTS_JS = PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js"


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
