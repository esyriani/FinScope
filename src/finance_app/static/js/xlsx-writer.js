const XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const XLSX_MONEY_FORMAT = '#,##0.00 [$$-C0C];-#,##0.00 [$$-C0C];"-" [$$-C0C]';
const XLSX_NUMBER_FORMAT = "#,##0.00";
const XLSX_MAX_TABLE_HEADER_LENGTH = 255;
const XLSX_TABLE_NAME = "Table1";
const XLSX_MONEY_HEADER_RE =
    /\b(amount|spending|income|balance|debit|credit|payment|paid|current|prior|change|delta|expected|actual|budget)\b|\$/i;
const XLSX_PERCENT_HEADER_RE = /\b(percent|percentage|share|rate)\b|%/i;
const XLSX_CURRENCY_RE = /[$\u20ac\u00a3\u00a5]/;
const XLSX_PERCENT_RE = /%/;
const XLSX_STRICT_NUMBER_RE = /^[+-]?(?:(?:\d+)|(?:\d{1,3}(?:[ ,]\d{3})+))(?:[.,]\d+)?(?:e[+-]?\d+)?$/i;
const XLSX_NUMBER_TOKEN_RE = /[+-]?\(?\d[\d ,.]*\)?/;

function xlsxNormalizeText(value) {
    return String(value || "")
        .replace(/\s+/g, " ")
        .trim();
}

function xlsxXmlEscape(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function xlsxSheetName(value) {
    const cleaned = xlsxNormalizeText(value).replace(/[:\\/?*[\]]/g, " ");
    return cleaned.slice(0, 31) || "Export";
}

function uniqueXlsxHeaders(values) {
    const used = new Map();
    const headers = values.length ? values : ["Column1"];

    return headers.map((value, index) => {
        const rawBase = xlsxNormalizeText(value) || `Column${index + 1}`;
        const base = rawBase.slice(0, XLSX_MAX_TABLE_HEADER_LENGTH);
        const key = base.toLocaleLowerCase();
        const count = used.get(key) || 0;
        used.set(key, count + 1);

        if (!count) {
            return base;
        }

        const suffix = String(count + 1);
        return `${base.slice(0, XLSX_MAX_TABLE_HEADER_LENGTH - suffix.length)}${suffix}`;
    });
}

function normalizeXlsxCell(cell) {
    return {
        explicitType: xlsxNormalizeText(cell?.explicitType || ""),
        explicitValue: xlsxNormalizeText(cell?.explicitValue ?? ""),
        text: xlsxNormalizeText(cell?.text ?? ""),
    };
}

function normalizeXlsxRows(rows, headers) {
    return rows
        .map((row) => headers.map((_header, index) => normalizeXlsxCell(row?.[index] || null)))
        .filter((row) => row.some((cell) => !isBlankXlsxValue(cell.text) || !isBlankXlsxValue(cell.explicitValue)));
}

function isBlankXlsxValue(value) {
    return xlsxNormalizeText(value) === "";
}

function normalizeXlsxNumberToken(value) {
    const raw = xlsxNormalizeText(value)
        .replace(/\u00a0/g, " ")
        .replace(/[\u2212\u2013\u2014]/g, "-");
    if (!raw) return null;

    const negativeParentheses = /^\(.*\)$/.test(raw);
    let cleaned = raw.replace(/[()]/g, "").replace(/[^\d,.\-+eE]/g, "");
    if (!cleaned || cleaned === "-" || cleaned === "+") return null;

    const commaCount = (cleaned.match(/,/g) || []).length;
    const dotCount = (cleaned.match(/\./g) || []).length;
    if (commaCount && dotCount) {
        cleaned = cleaned.replace(/,/g, "");
    } else if (commaCount === 1 && dotCount === 0) {
        cleaned = cleaned.replace(",", ".");
    } else if (commaCount > 1 && dotCount === 0) {
        cleaned = cleaned.replace(/,/g, "");
    }

    const parsed = Number(cleaned);
    if (!Number.isFinite(parsed)) return null;
    return negativeParentheses ? -Math.abs(parsed) : parsed;
}

function parseWholeXlsxNumberText(value) {
    const text = xlsxNormalizeText(value);
    if (!XLSX_STRICT_NUMBER_RE.test(text.replace(/\u00a0/g, " "))) {
        return null;
    }

    return normalizeXlsxNumberToken(text);
}

function parseFirstXlsxNumberText(value) {
    const match = xlsxNormalizeText(value).match(XLSX_NUMBER_TOKEN_RE);
    return match ? normalizeXlsxNumberToken(match[0]) : null;
}

function parsedXlsxValue(cell, headerName, sortType) {
    const explicitValue = xlsxNormalizeText(cell.explicitValue);
    const text = xlsxNormalizeText(cell.text);
    const source = explicitValue || text;

    if (isBlankXlsxValue(source)) {
        return { kind: "blank", value: null };
    }

    const explicitKind = cell.explicitType;
    const looksPercent =
        explicitKind === "percent" || XLSX_PERCENT_RE.test(text) || XLSX_PERCENT_HEADER_RE.test(headerName);
    const looksMoney = explicitKind === "money" || XLSX_CURRENCY_RE.test(text) || XLSX_MONEY_HEADER_RE.test(headerName);

    if (explicitValue) {
        const explicitNumber = normalizeXlsxNumberToken(explicitValue);
        if (explicitNumber !== null) {
            return {
                kind: explicitKind || (looksPercent ? "percent" : looksMoney ? "money" : "number"),
                value: looksPercent && XLSX_PERCENT_RE.test(explicitValue) ? explicitNumber / 100 : explicitNumber,
            };
        }
    }

    if (looksPercent) {
        const percentNumber = parseFirstXlsxNumberText(text);
        if (percentNumber !== null && XLSX_PERCENT_RE.test(text)) {
            return { kind: "percent", value: percentNumber / 100 };
        }
    }

    if (looksMoney) {
        const moneyNumber = XLSX_CURRENCY_RE.test(text)
            ? parseFirstXlsxNumberText(text)
            : parseWholeXlsxNumberText(text);
        if (moneyNumber !== null) {
            return { kind: "money", value: moneyNumber };
        }
    }

    const strictNumber = sortType === "number" ? parseFirstXlsxNumberText(text) : parseWholeXlsxNumberText(text);
    if (strictNumber !== null) {
        return { kind: "number", value: strictNumber };
    }

    return { kind: "string", value: text };
}

function analyzeXlsxColumns(headers, rows, parsedRows, sortTypes) {
    return headers.map((header, columnIndex) => {
        const parsedCells = parsedRows.map((row) => row[columnIndex]);
        const nonBlankCells = parsedCells.filter((cell) => cell.kind !== "blank");
        const numeric = nonBlankCells.length > 0 && nonBlankCells.every((cell) => cell.kind !== "string");
        const hasMoneyCells = nonBlankCells.some((cell) => cell.kind === "money");
        const hasPercentCells = nonBlankCells.some((cell) => cell.kind === "percent");
        const percent = numeric && !hasMoneyCells && (hasPercentCells || XLSX_PERCENT_HEADER_RE.test(header));
        const money = numeric && !percent && (hasMoneyCells || XLSX_MONEY_HEADER_RE.test(header));
        const type = money ? "money" : percent ? "percent" : numeric ? "number" : "string";
        const sortType = sortTypes[columnIndex] || "";
        const widthSamples = [
            header,
            ...rows.map((row) => row[columnIndex]?.text || row[columnIndex]?.explicitValue || ""),
        ];
        const maxLength = Math.max(8, ...widthSamples.map((value) => xlsxNormalizeText(value).length));

        return {
            type,
            sortType,
            total: numeric,
            width: Math.min(60, Math.max(8, maxLength + 2)),
        };
    });
}

function buildXlsxTableModel(source) {
    const headers = uniqueXlsxHeaders(Array.isArray(source?.headers) ? source.headers : []);
    const sortTypes = Array.isArray(source?.sortTypes) ? source.sortTypes : [];
    const rows = normalizeXlsxRows(Array.isArray(source?.rows) ? source.rows : [], headers);
    const parsedRows = rows.map((row) =>
        row.map((cell, columnIndex) => parsedXlsxValue(cell, headers[columnIndex], sortTypes[columnIndex]))
    );
    const columns = analyzeXlsxColumns(headers, rows, parsedRows, sortTypes);
    const hasTotalRow = columns.some((column) => column.total);
    const labelColumnIndex = columns.findIndex((column) => !column.total);

    return {
        columns,
        hasTotalRow,
        headers,
        labelColumnIndex,
        parsedRows,
        rows,
        totalLabel: source?.totalLabel || "Total",
    };
}

function xlsxColumnName(index) {
    let name = "";
    let value = index + 1;
    while (value > 0) {
        const remainder = (value - 1) % 26;
        name = String.fromCharCode(65 + remainder) + name;
        value = Math.floor((value - 1) / 26);
    }
    return name;
}

function xlsxCellReference(columnIndex, rowIndex) {
    return `${xlsxColumnName(columnIndex)}${rowIndex}`;
}

function xlsxRange(columnCount, rowCount) {
    return `A1:${xlsxColumnName(columnCount - 1)}${rowCount}`;
}

function xlsxStyleIdForType(type) {
    if (type === "money") return 1;
    if (type === "percent") return 2;
    if (type === "number") return 3;
    return 0;
}

function xlsxNumberText(value) {
    if (!Number.isFinite(value)) {
        return "0";
    }
    return String(Math.round((value + Number.EPSILON) * 1000000000000) / 1000000000000);
}

function xlsxStringCell(reference, value) {
    const text = xlsxXmlEscape(value);
    return `<c r="${reference}" t="inlineStr"><is><t>${text}</t></is></c>`;
}

function xlsxNumberCell(reference, value, styleId, formula = "") {
    const style = styleId ? ` s="${styleId}"` : "";
    const formulaXml = formula ? `<f>${xlsxXmlEscape(formula)}</f>` : "";
    return `<c r="${reference}"${style}>${formulaXml}<v>${xlsxNumberText(value)}</v></c>`;
}

function xlsxWorksheetRows(model) {
    const rows = [
        `<row r="1" spans="1:${model.headers.length}">${model.headers
            .map((header, columnIndex) => xlsxStringCell(xlsxCellReference(columnIndex, 1), header))
            .join("")}</row>`,
    ];

    model.rows.forEach((row, rowIndex) => {
        const excelRowIndex = rowIndex + 2;
        const cells = row
            .map((cell, columnIndex) => {
                const reference = xlsxCellReference(columnIndex, excelRowIndex);
                const parsed = model.parsedRows[rowIndex][columnIndex];
                const column = model.columns[columnIndex];

                if (parsed.kind === "blank") {
                    return "";
                }
                if (column.total && parsed.kind !== "string") {
                    return xlsxNumberCell(reference, parsed.value, xlsxStyleIdForType(column.type));
                }
                return xlsxStringCell(reference, cell.text);
            })
            .join("");
        rows.push(`<row r="${excelRowIndex}" spans="1:${model.headers.length}">${cells}</row>`);
    });

    if (model.hasTotalRow) {
        const totalRowIndex = model.rows.length + 2;
        const firstDataRow = 2;
        const lastDataRow = model.rows.length + 1;
        const cells = model.columns
            .map((column, columnIndex) => {
                const reference = xlsxCellReference(columnIndex, totalRowIndex);
                if (column.total) {
                    const sum = model.parsedRows.reduce((total, row) => total + (row[columnIndex].value || 0), 0);
                    const columnName = xlsxColumnName(columnIndex);
                    const formula = `SUBTOTAL(109,${columnName}${firstDataRow}:${columnName}${lastDataRow})`;
                    return xlsxNumberCell(reference, sum, xlsxStyleIdForType(column.type), formula);
                }
                if (columnIndex === model.labelColumnIndex) {
                    return xlsxStringCell(reference, model.totalLabel);
                }
                return "";
            })
            .join("");
        rows.push(`<row r="${totalRowIndex}" spans="1:${model.headers.length}">${cells}</row>`);
    }

    return rows.join("");
}

function xlsxWorksheetXml(model) {
    const dataEndRow = model.rows.length + 1;
    const totalRowCount = model.hasTotalRow ? 1 : 0;
    const rowCount = dataEndRow + totalRowCount;
    const dimension = xlsxRange(model.headers.length, rowCount);
    const columns = model.columns
        .map(
            (column, index) =>
                `<col min="${index + 1}" max="${index + 1}" width="${column.width}" bestFit="1" customWidth="1"/>`
        )
        .join("");

    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <dimension ref="${dimension}"/>
    <sheetViews><sheetView tabSelected="1" workbookViewId="0"/></sheetViews>
    <sheetFormatPr defaultRowHeight="15"/>
    <cols>${columns}</cols>
    <sheetData>${xlsxWorksheetRows(model)}</sheetData>
    <tableParts count="1"><tablePart r:id="rId1"/></tableParts>
</worksheet>`;
}

function xlsxTableXml(model) {
    const dataEndRow = model.rows.length + 1;
    const rowCount = dataEndRow + (model.hasTotalRow ? 1 : 0);
    const tableRef = xlsxRange(model.headers.length, rowCount);
    const autoFilterRef = xlsxRange(model.headers.length, dataEndRow);
    const totalAttrs = model.hasTotalRow ? 'totalsRowCount="1"' : 'totalsRowShown="0"';
    const columns = model.headers
        .map((header, index) => {
            const column = model.columns[index];
            const attrs = [`id="${index + 1}"`, `name="${xlsxXmlEscape(header)}"`];
            if (model.hasTotalRow && column.total) {
                attrs.push('totalsRowFunction="sum"');
            } else if (model.hasTotalRow && index === model.labelColumnIndex) {
                attrs.push(`totalsRowLabel="${xlsxXmlEscape(model.totalLabel)}"`);
            }
            return `<tableColumn ${attrs.join(" ")}/>`;
        })
        .join("");

    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" id="1" name="${XLSX_TABLE_NAME}" displayName="${XLSX_TABLE_NAME}" ref="${tableRef}" ${totalAttrs}>
    <autoFilter ref="${autoFilterRef}"/>
    <tableColumns count="${model.headers.length}">${columns}</tableColumns>
    <tableStyleInfo name="TableStyleLight1" showFirstColumn="0" showLastColumn="0" showRowStripes="1" showColumnStripes="0"/>
</table>`;
}

function xlsxStylesXml() {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
    <numFmts count="2">
        <numFmt numFmtId="164" formatCode="${xlsxXmlEscape(XLSX_MONEY_FORMAT)}"/>
        <numFmt numFmtId="165" formatCode="${xlsxXmlEscape(XLSX_NUMBER_FORMAT)}"/>
    </numFmts>
    <fonts count="1"><font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/><scheme val="minor"/></font></fonts>
    <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
    <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
    <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
    <cellXfs count="4">
        <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
        <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
        <xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
        <xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    </cellXfs>
    <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
    <dxfs count="0"/>
    <tableStyles count="1" defaultTableStyle="TableStyleLight1" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>`;
}

function xlsxWorkbookXml(sheetName) {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <bookViews><workbookView/></bookViews>
    <sheets><sheet name="${xlsxXmlEscape(xlsxSheetName(sheetName))}" sheetId="1" r:id="rId1"/></sheets>
</workbook>`;
}

function xlsxWorkbookRelationshipsXml() {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
    <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`;
}

function xlsxWorksheetRelationshipsXml() {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/table" Target="../tables/table1.xml"/>
</Relationships>`;
}

function xlsxPackageRelationshipsXml() {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>`;
}

function xlsxContentTypesXml() {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
    <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
    <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
    <Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>
</Types>`;
}

function xlsxPackageFiles(model, sheetName) {
    return [
        { name: "[Content_Types].xml", content: xlsxContentTypesXml() },
        { name: "_rels/.rels", content: xlsxPackageRelationshipsXml() },
        { name: "xl/workbook.xml", content: xlsxWorkbookXml(sheetName) },
        { name: "xl/_rels/workbook.xml.rels", content: xlsxWorkbookRelationshipsXml() },
        { name: "xl/worksheets/sheet1.xml", content: xlsxWorksheetXml(model) },
        { name: "xl/worksheets/_rels/sheet1.xml.rels", content: xlsxWorksheetRelationshipsXml() },
        { name: "xl/tables/table1.xml", content: xlsxTableXml(model) },
        { name: "xl/styles.xml", content: xlsxStylesXml() },
    ];
}

const XLSX_CRC32_TABLE = Array.from({ length: 256 }, (_value, index) => {
    let crc = index;
    for (let bit = 0; bit < 8; bit += 1) {
        crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
    }
    return crc >>> 0;
});

function xlsxCrc32(bytes) {
    let crc = 0xffffffff;
    bytes.forEach((byte) => {
        crc = XLSX_CRC32_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    });
    return (crc ^ 0xffffffff) >>> 0;
}

function writeXlsxUint16(target, offset, value) {
    target[offset] = value & 0xff;
    target[offset + 1] = (value >>> 8) & 0xff;
}

function writeXlsxUint32(target, offset, value) {
    target[offset] = value & 0xff;
    target[offset + 1] = (value >>> 8) & 0xff;
    target[offset + 2] = (value >>> 16) & 0xff;
    target[offset + 3] = (value >>> 24) & 0xff;
}

function xlsxZipDateTime(date = new Date()) {
    const year = Math.min(2107, Math.max(1980, date.getFullYear()));
    return {
        date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
        time: (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2),
    };
}

function createXlsxZipBlob(files, mimeType) {
    const encoder = new TextEncoder();
    const timestamp = xlsxZipDateTime();
    const localParts = [];
    const centralParts = [];
    let offset = 0;

    files.forEach((file) => {
        const nameBytes = encoder.encode(file.name);
        const contentBytes = typeof file.content === "string" ? encoder.encode(file.content) : file.content;
        const checksum = xlsxCrc32(contentBytes);
        const localHeader = new Uint8Array(30 + nameBytes.length);
        writeXlsxUint32(localHeader, 0, 0x04034b50);
        writeXlsxUint16(localHeader, 4, 20);
        writeXlsxUint16(localHeader, 6, 0x0800);
        writeXlsxUint16(localHeader, 8, 0);
        writeXlsxUint16(localHeader, 10, timestamp.time);
        writeXlsxUint16(localHeader, 12, timestamp.date);
        writeXlsxUint32(localHeader, 14, checksum);
        writeXlsxUint32(localHeader, 18, contentBytes.length);
        writeXlsxUint32(localHeader, 22, contentBytes.length);
        writeXlsxUint16(localHeader, 26, nameBytes.length);
        writeXlsxUint16(localHeader, 28, 0);
        localHeader.set(nameBytes, 30);
        localParts.push(localHeader, contentBytes);

        const centralHeader = new Uint8Array(46 + nameBytes.length);
        writeXlsxUint32(centralHeader, 0, 0x02014b50);
        writeXlsxUint16(centralHeader, 4, 20);
        writeXlsxUint16(centralHeader, 6, 20);
        writeXlsxUint16(centralHeader, 8, 0x0800);
        writeXlsxUint16(centralHeader, 10, 0);
        writeXlsxUint16(centralHeader, 12, timestamp.time);
        writeXlsxUint16(centralHeader, 14, timestamp.date);
        writeXlsxUint32(centralHeader, 16, checksum);
        writeXlsxUint32(centralHeader, 20, contentBytes.length);
        writeXlsxUint32(centralHeader, 24, contentBytes.length);
        writeXlsxUint16(centralHeader, 28, nameBytes.length);
        writeXlsxUint16(centralHeader, 30, 0);
        writeXlsxUint16(centralHeader, 32, 0);
        writeXlsxUint16(centralHeader, 34, 0);
        writeXlsxUint16(centralHeader, 36, 0);
        writeXlsxUint32(centralHeader, 38, 0);
        writeXlsxUint32(centralHeader, 42, offset);
        centralHeader.set(nameBytes, 46);
        centralParts.push(centralHeader);

        offset += localHeader.length + contentBytes.length;
    });

    const centralDirectoryOffset = offset;
    const centralDirectorySize = centralParts.reduce((total, part) => total + part.length, 0);
    const endRecord = new Uint8Array(22);
    writeXlsxUint32(endRecord, 0, 0x06054b50);
    writeXlsxUint16(endRecord, 4, 0);
    writeXlsxUint16(endRecord, 6, 0);
    writeXlsxUint16(endRecord, 8, files.length);
    writeXlsxUint16(endRecord, 10, files.length);
    writeXlsxUint32(endRecord, 12, centralDirectorySize);
    writeXlsxUint32(endRecord, 16, centralDirectoryOffset);
    writeXlsxUint16(endRecord, 20, 0);

    return new Blob([...localParts, ...centralParts, endRecord], { type: mimeType });
}

function createXlsxBlob(source, sheetName) {
    return createXlsxZipBlob(xlsxPackageFiles(buildXlsxTableModel(source), sheetName), XLSX_MIME_TYPE);
}

window.financeXlsxWriter = {
    createXlsxBlob,
};
