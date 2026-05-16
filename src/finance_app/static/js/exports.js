function normalizeExportText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
}

function slugifyExportName(value) {
    const slug = normalizeExportText(value)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");

    return slug || "finance-export";
}

function exportDateStamp() {
    return new Date().toISOString().slice(0, 10);
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

function downloadDataUrl(dataUrl, filename) {
    const link = document.createElement("a");
    link.href = dataUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
}

function exportIcon(type) {
    if (type === "image") {
        return `
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <rect x="3" y="5" width="18" height="14" rx="2"></rect>
                <circle cx="8.5" cy="10" r="1.5"></circle>
                <path d="m21 15-5-5L5 19"></path>
            </svg>
        `;
    }

    return `
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 3v12"></path>
            <path d="m7 10 5 5 5-5"></path>
            <path d="M5 21h14"></path>
        </svg>
    `;
}

function createExportButton(label, type, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-sm btn-outline-secondary export-button";
    button.innerHTML = `${exportIcon(type)}<span>${label}</span>`;
    button.setAttribute("aria-label", financeTranslate("Export {label}", { label }));
    button.addEventListener("click", onClick);
    return button;
}

function createExportToolbar() {
    const toolbar = document.createElement("div");
    toolbar.className = "export-toolbar";
    return toolbar;
}

function elementExportTitle(element, fallback) {
    if (element.dataset.exportTitle) return normalizeExportText(element.dataset.exportTitle);

    const card = element.closest(".card");
    const localTitle = card?.querySelector(".card-title, .card-header h5, .card-header h6");
    const pageTitle = document.querySelector("h2");

    return normalizeExportText(localTitle?.textContent || pageTitle?.textContent || fallback);
}

function formControlExportValue(control) {
    if (control.matches("select")) {
        return normalizeExportText(control.selectedOptions[0]?.textContent || control.value);
    }

    if (control.matches("textarea")) {
        return control.value;
    }

    if (control.matches("input")) {
        const type = String(control.type || "").toLowerCase();

        if (type === "hidden" || type === "submit" || type === "button") {
            return "";
        }

        if (type === "checkbox" || type === "radio") {
            return control.checked ? control.value : "";
        }

        return control.value;
    }

    return "";
}

function cellExportText(cell) {
    const clone = cell.cloneNode(true);
    const sourceControls = Array.from(cell.querySelectorAll("select, textarea, input"));
    const clonedControls = Array.from(clone.querySelectorAll("select, textarea, input"));

    clonedControls.forEach((control, index) => {
        control.replaceWith(document.createTextNode(formControlExportValue(sourceControls[index] || control)));
    });

    clone.querySelectorAll("button, svg, script, style").forEach((node) => node.remove());
    return normalizeExportText(clone.textContent);
}

function tableMatrix(table) {
    return Array.from(table.querySelectorAll("tr"))
        .map((row) => Array.from(row.cells).map(cellExportText))
        .filter((row) => row.some((value) => value !== ""));
}

function csvEscape(value) {
    const text = String(value ?? "");
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function exportTableCsv(table, filenameBase) {
    const csv = tableMatrix(table)
        .map((row) => row.map(csvEscape).join(","))
        .join("\r\n");

    downloadBlob(
        new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" }),
        `${filenameBase}.csv`
    );
}

function xmlEscape(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function excelSheetName(value) {
    const cleaned = normalizeExportText(value).replace(/[:\\/?*\[\]]/g, " ");
    return cleaned.slice(0, 31) || "Export";
}

function exportTableExcel(table, filenameBase, sheetName) {
    const rows = tableMatrix(table)
        .map((row) => {
            const cells = row
                .map((value) => `<Cell><Data ss:Type="String">${xmlEscape(value)}</Data></Cell>`)
                .join("");

            return `<Row>${cells}</Row>`;
        })
        .join("");

    const workbook = `<?xml version="1.0"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
    xmlns:o="urn:schemas-microsoft-com:office:office"
    xmlns:x="urn:schemas-microsoft-com:office:excel"
    xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
    <Worksheet ss:Name="${xmlEscape(excelSheetName(sheetName))}">
        <Table>${rows}</Table>
    </Worksheet>
</Workbook>`;

    downloadBlob(
        new Blob([workbook], { type: "application/vnd.ms-excel;charset=utf-8" }),
        `${filenameBase}.${table.dataset.exportExcelExtension || "xls"}`
    );
}

function insertTableExportToolbar(table, toolbar) {
    const card = table.closest(".card");
    const localTitleBar = table.closest("[data-table-export-scope]")?.querySelector(".section-title");
    const titleBar = localTitleBar || card?.querySelector(".section-title");
    const cardHeader = card?.querySelector(".card-header");
    const tableResponsive = table.closest(".table-responsive");

    if (titleBar) {
        titleBar.appendChild(toolbar);
        return;
    }

    if (cardHeader) {
        cardHeader.appendChild(toolbar);
        return;
    }

    toolbar.classList.add("table-export-toolbar");
    (tableResponsive || table).before(toolbar);
}

function setupTableExports() {
    Array.from(document.querySelectorAll("table")).forEach((table, index) => {
        if (table.hasAttribute("data-no-export")) return;
        if (table.dataset.exportReady === "true") return;

        table.dataset.exportReady = "true";
        const title = elementExportTitle(table, financeTranslate("Table {number}", { number: index + 1 }));
        const filenameBase = table.dataset.exportFilenameBase || `${slugifyExportName(title)}-${index + 1}-${exportDateStamp()}`;
        const toolbar = createExportToolbar();

        toolbar.appendChild(createExportButton(financeTranslate("CSV"), "download", () => exportTableCsv(table, filenameBase)));
        toolbar.appendChild(createExportButton(financeTranslate("Excel"), "download", () => exportTableExcel(table, filenameBase, title)));
        insertTableExportToolbar(table, toolbar);
    });
}

function cssVariable(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

function exportCanvasPng(canvas, filenameBase) {
    const exportCanvas = document.createElement("canvas");
    exportCanvas.width = canvas.width;
    exportCanvas.height = canvas.height;

    const context = exportCanvas.getContext("2d");
    context.fillStyle = cssVariable("--chart-surface", cssVariable("--app-surface", "#ffffff"));
    context.fillRect(0, 0, exportCanvas.width, exportCanvas.height);
    context.drawImage(canvas, 0, 0);

    exportCanvas.toBlob((blob) => {
        if (blob) {
            downloadBlob(blob, `${filenameBase}.png`);
        }
    }, "image/png");
}

function exportEChartPng(container, filenameBase) {
    const chart = window.echarts?.getInstanceByDom(container);
    if (!chart) return;

    downloadDataUrl(
        chart.getDataURL({
            type: "png",
            pixelRatio: 2,
            backgroundColor: cssVariable("--chart-surface", cssVariable("--app-surface", "#ffffff")),
        }),
        `${filenameBase}.png`
    );
}

function insertChartExportToolbar(element, toolbar) {
    const localTitleBar = element.closest("[data-chart-export-scope]")?.querySelector(".section-title");
    const titleBar = localTitleBar || element.closest(".card")?.querySelector(".section-title");

    if (titleBar) {
        titleBar.appendChild(toolbar);
    } else {
        toolbar.classList.add("chart-export-toolbar");
        element.before(toolbar);
    }
}

function setupChartExports() {
    const chartContainers = Array.from(document.querySelectorAll("[data-chart-export]"));
    const chartCanvases = new Set();

    chartContainers.forEach((container) => {
        container.querySelectorAll("canvas").forEach((canvas) => chartCanvases.add(canvas));
    });

    chartContainers.forEach((container, index) => {
        if (container.dataset.exportReady === "true") return;

        const chart = window.echarts?.getInstanceByDom(container);
        if (!chart) return;

        container.dataset.exportReady = "true";
        const title = elementExportTitle(container, financeTranslate("Chart {number}", { number: index + 1 }));
        const filenameBase = `${slugifyExportName(title)}-${index + 1}-${exportDateStamp()}`;
        const toolbar = createExportToolbar();

        toolbar.appendChild(createExportButton("PNG", "image", () => exportEChartPng(container, filenameBase)));
        insertChartExportToolbar(container, toolbar);
    });

    Array.from(document.querySelectorAll("canvas")).forEach((canvas, index) => {
        if (chartCanvases.has(canvas)) return;
        if (canvas.dataset.exportReady === "true") return;

        canvas.dataset.exportReady = "true";
        const title = elementExportTitle(canvas, financeTranslate("Chart {number}", { number: index + 1 }));
        const filenameBase = `${slugifyExportName(title)}-${index + 1}-${exportDateStamp()}`;
        const toolbar = createExportToolbar();

        toolbar.appendChild(createExportButton("PNG", "image", () => exportCanvasPng(canvas, filenameBase)));
        insertChartExportToolbar(canvas, toolbar);
    });
}

setupTableExports();
setupChartExports();
