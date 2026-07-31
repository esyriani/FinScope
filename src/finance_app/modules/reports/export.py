"""Server-side export helpers for Reports.

Exports are generated from the same overview view model used by the page, so
downloaded files reflect the active report filters without relying on browser
table state.
"""

import csv
import io
import zipfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from finance_app.core.i18n import gettext

XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXPORT_COLUMNS = (
    "section",
    "label",
    "spending",
    "income_and_credits",
    "net_cash_flow",
    "transactions",
    "share_percent",
)
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def reports_overview_csv(rows: Sequence[Mapping[str, Any]]) -> str:
    """Return Reports overview rows as CSV text."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    writer.writerow({column: export_header(column) for column in EXPORT_COLUMNS})
    for row in rows:
        writer.writerow({column: csv_export_value(row.get(column, "")) for column in EXPORT_COLUMNS})
    return output.getvalue()


def csv_export_value(value: object) -> object:
    """Return a CSV-safe cell value that neutralizes spreadsheet formulas."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(CSV_FORMULA_PREFIXES) else value


def export_header(column: str) -> str:
    """Return a localized export header for a canonical column key."""
    labels = {
        "section": "Section",
        "label": "Label",
        "spending": "Spending",
        "income_and_credits": "Income and credits",
        "net_cash_flow": "Net cash flow",
        "transactions": "Transactions",
        "share_percent": "Share %",
    }
    return gettext(labels[column])


def reports_overview_xlsx(rows: Sequence[Mapping[str, Any]]) -> bytes:
    """Return Reports overview rows as a minimal XLSX workbook."""
    table = [[export_header(column) for column in EXPORT_COLUMNS]]
    table.extend([[row.get(column, "") for column in EXPORT_COLUMNS] for row in rows])
    worksheet = worksheet_xml(table)

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml())
        archive.writestr("_rels/.rels", package_relationships_xml())
        archive.writestr("xl/workbook.xml", workbook_xml())
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_relationships_xml())
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        archive.writestr("xl/styles.xml", styles_xml())
    return output.getvalue()


def worksheet_xml(table: Sequence[Sequence[Any]]) -> str:
    """Return worksheet XML using inline strings and numeric cells."""
    rows = []
    for row_index, row in enumerate(table, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            reference = f"{column_name(column_index)}{row_index}"
            cells.append(cell_xml(reference, value))
        rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    dimension = f"A1:{column_name(len(EXPORT_COLUMNS))}{max(1, len(table))}"
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
    <dimension ref="{dimension}"/>
    <sheetViews><sheetView workbookViewId="0"/></sheetViews>
    <sheetFormatPr defaultRowHeight="15"/>
    <sheetData>{"".join(rows)}</sheetData>
</worksheet>"""


def cell_xml(reference: str, value: Any) -> str:
    """Return one XLSX cell as XML."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}" s="1"><v>{value}</v></c>'
    text = "" if value is None else str(value)
    return f'<c r="{reference}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'


def column_name(index: int) -> str:
    """Return a one-based Excel column name."""
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def content_types_xml() -> str:
    """Return XLSX content type metadata."""
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
    <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
    <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""


def package_relationships_xml() -> str:
    """Return package relationship metadata."""
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def workbook_relationships_xml() -> str:
    """Return workbook relationship metadata."""
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
    <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def workbook_xml() -> str:
    """Return workbook XML."""
    sheet_name = escape(gettext("Overview"))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <bookViews><workbookView/></bookViews>
    <sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""


def styles_xml() -> str:
    """Return workbook styles XML."""
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
    <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
    <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
    <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
    <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
    <cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
    <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def report_export_filename(extension: str, stem: str = "reports-overview") -> str:
    """Return a dated Reports export filename."""
    return f"{stem}-{datetime.now().date().isoformat()}.{extension}"
