function normalizeExportText(value) {
    return String(value || "")
        .replace(/\s+/g, " ")
        .trim();
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
    if (type === "expand") {
        return `
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path d="M8 3H3v5"></path>
                <path d="M3 3l7 7"></path>
                <path d="M16 3h5v5"></path>
                <path d="M21 3l-7 7"></path>
                <path d="M8 21H3v-5"></path>
                <path d="M3 21l7-7"></path>
                <path d="M16 21h5v-5"></path>
                <path d="M21 21l-7-7"></path>
            </svg>
        `;
    }

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

function createToolbarButton(label, type, onClick, ariaLabel = label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-sm btn-outline-secondary export-button";
    button.innerHTML = `${exportIcon(type)}<span>${label}</span>`;
    button.setAttribute("aria-label", ariaLabel);
    button.addEventListener("click", onClick);
    return button;
}

function createIconToolbarButton(label, type, onClick, ariaLabel = label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-sm btn-outline-secondary export-button export-button-icon-only";
    button.innerHTML = exportIcon(type);
    button.title = label;
    button.setAttribute("aria-label", ariaLabel);
    button.addEventListener("click", onClick);
    return button;
}

function createExportButton(label, type, onClick) {
    return createToolbarButton(label, type, onClick, financeTranslate("Export {label}", { label }));
}

function createExpandButton(title, onClick) {
    return createIconToolbarButton(
        financeTranslate("Expand"),
        "expand",
        onClick,
        financeTranslate("Expand {label}", { label: title })
    );
}

function createExportToolbar() {
    const toolbar = document.createElement("div");
    toolbar.className = "export-toolbar";
    return toolbar;
}

let expandedExportState = null;

function resizeChartElement(element) {
    const chart = window.echarts?.getInstanceByDom(element);
    if (chart) {
        chart.resize();
    }
}

function restoreExpandedExportContent() {
    if (!expandedExportState) return;

    const state = expandedExportState;
    expandedExportState = null;
    state.element.classList.remove("export-expanded-element", `export-expanded-${state.kind}`);

    if (state.placeholder.parentNode) {
        state.placeholder.replaceWith(state.element);
    } else if (state.parent) {
        state.parent.insertBefore(state.element, state.nextSibling);
    }

    if (state.kind === "chart") {
        requestAnimationFrame(() => resizeChartElement(state.element));
    }
}

function closeExpandedExportModal() {
    const modalElement = document.getElementById("export-expand-modal");
    if (!modalElement || !expandedExportState) return Promise.resolve();

    if (!window.bootstrap?.Modal) {
        restoreExpandedExportContent();
        modalElement.style.display = "none";
        modalElement.setAttribute("aria-hidden", "true");
        return Promise.resolve();
    }

    const modal = bootstrap.Modal.getInstance(modalElement);
    if (!modal || !modalElement.classList.contains("show")) {
        restoreExpandedExportContent();
        return Promise.resolve();
    }

    return new Promise((resolve) => {
        modalElement.addEventListener("hidden.bs.modal", resolve, { once: true });
        modal.hide();
    });
}

function showModalAfterExpandedExportCloses(modalElement) {
    if (!window.bootstrap?.Modal) return;

    closeExpandedExportModal().then(() => {
        bootstrap.Modal.getOrCreateInstance(modalElement).show();
    });
}

function ensureExportExpandModal() {
    const existing = document.getElementById("export-expand-modal");
    if (existing) return existing;

    const modal = document.createElement("div");
    modal.className = "modal fade export-expand-modal";
    modal.id = "export-expand-modal";
    modal.tabIndex = -1;
    modal.setAttribute("aria-labelledby", "export-expand-title");
    modal.setAttribute("aria-hidden", "true");
    modal.innerHTML = `
        <div class="modal-dialog modal-dialog-centered export-expand-dialog">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="export-expand-title" data-export-expand-title></h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="${financeTranslate("Close")}"></button>
                </div>
                <div class="modal-body" data-export-expand-body></div>
            </div>
        </div>
    `;
    modal.addEventListener("hidden.bs.modal", restoreExpandedExportContent);
    modal.querySelector(".btn-close")?.addEventListener("click", () => {
        if (!window.bootstrap?.Modal) {
            restoreExpandedExportContent();
            modal.style.display = "none";
        }
    });
    document.body.appendChild(modal);
    return modal;
}

function showExpandedExportModal(modalElement, kind, element) {
    if (!window.bootstrap?.Modal) {
        modalElement.style.display = "block";
        modalElement.removeAttribute("aria-hidden");
        if (kind === "chart") {
            requestAnimationFrame(() => resizeChartElement(element));
        }
        return;
    }

    modalElement.addEventListener(
        "shown.bs.modal",
        () => {
            if (kind === "chart") {
                resizeChartElement(element);
            }
        },
        { once: true }
    );
    bootstrap.Modal.getOrCreateInstance(modalElement).show();
}

function expandExportElement(element, title, kind) {
    const modalElement = ensureExportExpandModal();
    const modalBody = modalElement.querySelector("[data-export-expand-body]");
    const modalTitle = modalElement.querySelector("[data-export-expand-title]");
    const parent = element.parentNode;
    if (!modalBody || !modalTitle || !parent) return;

    restoreExpandedExportContent();

    const placeholder = document.createElement("div");
    const nextSibling = element.nextSibling;
    placeholder.hidden = true;
    placeholder.setAttribute("data-export-expand-placeholder", "");
    parent.insertBefore(placeholder, element);
    element.classList.add("export-expanded-element", `export-expanded-${kind}`);
    modalTitle.textContent = title;
    modalBody.replaceChildren(element);
    expandedExportState = {
        element,
        kind,
        nextSibling,
        parent,
        placeholder,
    };

    showExpandedExportModal(modalElement, kind, element);
}

function tableVisibleSource(table) {
    const selector = table.dataset.exportVisibleSource;
    if (!selector) return null;
    return document.querySelector(selector);
}

function expandTable(table, title) {
    const sourceTable = tableVisibleSource(table) || table;
    expandExportElement(sourceTable.closest(".table-responsive") || sourceTable, title, "table");
}

function expandChart(element, title) {
    expandExportElement(element, title, "chart");
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

const CSV_FORMULA_PREFIX_RE = /^[=+\-@\t\r]/;

function sanitizeCsvFormulaValue(value) {
    const text = String(value ?? "");
    return CSV_FORMULA_PREFIX_RE.test(text) ? `'${text}` : text;
}

function csvEscape(value) {
    const text = sanitizeCsvFormulaValue(value);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function isExportHeaderRow(row) {
    return Boolean(row.closest("thead, tfoot"));
}

function rowExportId(row) {
    return row.dataset.exportRowId || row.dataset.recurringId || "";
}

function isDisplayedExportRow(row) {
    if (row.hidden || row.closest("[hidden]")) return false;

    const rowStyle = window.getComputedStyle(row);
    if (rowStyle.display === "none" || rowStyle.visibility === "hidden") return false;

    return true;
}

function visibleExportSourceIds(table) {
    const sourceSelector = table.dataset.exportVisibleSource;
    if (!sourceSelector) return null;

    const source = document.querySelector(sourceSelector);
    if (!source) return null;

    const ids = Array.from(source.querySelectorAll("tbody tr"))
        .filter(isDisplayedExportRow)
        .map(rowExportId)
        .filter(Boolean);

    return new Set(ids);
}

function tableRowsForExport(table, scope) {
    const rows = Array.from(table.querySelectorAll("tr"));
    if (scope !== "displayed") return rows;

    const visibleSourceIds = visibleExportSourceIds(table);
    return rows.filter((row) => {
        if (isExportHeaderRow(row)) return true;
        if (visibleSourceIds) return visibleSourceIds.has(rowExportId(row));
        return isDisplayedExportRow(row);
    });
}

function tableMatrix(table, scope = "all") {
    return tableRowsForExport(table, scope)
        .map((row) => Array.from(row.cells).map(cellExportText))
        .filter((row) => row.some((value) => value !== ""));
}

function ensureTableExportChoiceModal() {
    const existing = document.getElementById("table-export-choice-modal");
    if (existing) return existing;

    const modal = document.createElement("div");
    modal.className = "modal fade";
    modal.id = "table-export-choice-modal";
    modal.tabIndex = -1;
    modal.setAttribute("aria-labelledby", "table-export-choice-title");
    modal.setAttribute("aria-hidden", "true");
    modal.innerHTML = `
        <div class="modal-dialog">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="table-export-choice-title">${financeTranslate("Export rows")}</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="${financeTranslate("Close")}"></button>
                </div>
                <div class="modal-body">
                    <p class="mb-0">${financeTranslate("Choose which rows to export from this table.")}</p>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-outline-secondary" type="button" data-bs-dismiss="modal">
                        <i class="bi bi-x-circle" aria-hidden="true"></i>${financeTranslate("Cancel")}
                    </button>
                    <button class="btn btn-outline-primary" type="button" data-export-choice="all">
                        <i class="bi bi-table" aria-hidden="true"></i>${financeTranslate("Entire table")}
                    </button>
                    <button class="btn btn-primary" type="button" data-export-choice="displayed">
                        <i class="bi bi-eye" aria-hidden="true"></i>${financeTranslate("Displayed rows")}
                    </button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    return modal;
}

function chooseTableExportScope() {
    const modalElement = ensureTableExportChoiceModal();
    if (!window.bootstrap?.Modal) {
        return Promise.resolve(
            window.confirm(financeTranslate("Export displayed rows only? Choose Cancel to export the entire table."))
                ? "displayed"
                : "all"
        );
    }

    const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
    return new Promise((resolve) => {
        let resolved = false;

        function finish(value) {
            if (resolved) return;
            resolved = true;
            modalElement.querySelectorAll("[data-export-choice]").forEach((button) => {
                button.removeEventListener("click", onChoiceClick);
            });
            modalElement.removeEventListener("hidden.bs.modal", onHidden);
            modal.hide();
            resolve(value);
        }

        function onChoiceClick(event) {
            finish(event.currentTarget.dataset.exportChoice || "");
        }

        function onHidden() {
            finish("");
        }

        modalElement.querySelectorAll("[data-export-choice]").forEach((button) => {
            button.addEventListener("click", onChoiceClick);
        });
        modalElement.addEventListener("hidden.bs.modal", onHidden, { once: true });
        modal.show();
    });
}

async function exportTableCsv(table, filenameBase) {
    const scope = await chooseTableExportScope();
    if (!scope) return;

    const csv = tableMatrix(table, scope)
        .map((row) => row.map(csvEscape).join(","))
        .join("\r\n");

    downloadBlob(new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" }), `${filenameBase}.csv`);
}

function xmlEscape(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function excelSheetName(value) {
    const cleaned = normalizeExportText(value).replace(/[:\\/?*[\]]/g, " ");
    return cleaned.slice(0, 31) || "Export";
}

async function exportTableExcel(table, filenameBase, sheetName) {
    const scope = await chooseTableExportScope();
    if (!scope) return;

    const rows = tableMatrix(table, scope)
        .map((row) => {
            const cells = row.map((value) => `<Cell><Data ss:Type="String">${xmlEscape(value)}</Data></Cell>`).join("");

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
    const toolbarTable = tableVisibleSource(table) || table;
    const card = toolbarTable.closest(".card");
    const localTitleBar = toolbarTable.closest("[data-table-export-scope]")?.querySelector(".section-title");
    const titleBar = localTitleBar || card?.querySelector(".section-title");
    const cardHeader = card?.querySelector(".card-header");
    const tableResponsive = toolbarTable.closest(".table-responsive");

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

function exportElements(root, selector) {
    return [...(root.matches?.(selector) ? [root] : []), ...Array.from(root.querySelectorAll(selector))];
}

function setupTableExports(root = document) {
    exportElements(root, "table").forEach((table, index) => {
        if (table.hasAttribute("data-no-export")) return;
        if (table.dataset.exportReady === "true") return;

        table.dataset.exportReady = "true";
        const title = elementExportTitle(table, financeTranslate("Table {number}", { number: index + 1 }));
        const filenameBase =
            table.dataset.exportFilenameBase || `${slugifyExportName(title)}-${index + 1}-${exportDateStamp()}`;
        const toolbar = createExportToolbar();
        toolbar.classList.add("table-export-toolbar");

        toolbar.appendChild(
            createExportButton(financeTranslate("CSV"), "download", () => exportTableCsv(table, filenameBase))
        );
        toolbar.appendChild(
            createExportButton(financeTranslate("Excel"), "download", () =>
                exportTableExcel(table, filenameBase, title)
            )
        );
        toolbar.appendChild(createExpandButton(title, () => expandTable(table, title)));
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

function setupChartExports(root = document) {
    const chartContainers = exportElements(root, "[data-chart-export]");
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
        toolbar.classList.add("chart-export-toolbar");

        toolbar.appendChild(createExportButton("PNG", "image", () => exportEChartPng(container, filenameBase)));
        toolbar.appendChild(createExpandButton(title, () => expandChart(container, title)));
        insertChartExportToolbar(container, toolbar);
    });

    exportElements(root, "canvas").forEach((canvas, index) => {
        if (chartCanvases.has(canvas)) return;
        if (canvas.dataset.exportReady === "true") return;

        canvas.dataset.exportReady = "true";
        const title = elementExportTitle(canvas, financeTranslate("Chart {number}", { number: index + 1 }));
        const filenameBase = `${slugifyExportName(title)}-${index + 1}-${exportDateStamp()}`;
        const toolbar = createExportToolbar();
        toolbar.classList.add("chart-export-toolbar");

        toolbar.appendChild(createExportButton("PNG", "image", () => exportCanvasPng(canvas, filenameBase)));
        toolbar.appendChild(createExpandButton(title, () => expandChart(canvas, title)));
        insertChartExportToolbar(canvas, toolbar);
    });
}

window.financeApp?.registerInitializer("exports.tables", setupTableExports);
window.financeApp?.registerInitializer("exports.charts", setupChartExports);
window.financeApp = window.financeApp || {};
window.financeApp.closeExpandedExportModal = closeExpandedExportModal;
window.financeApp.showModalAfterExpandedExportCloses = showModalAfterExpandedExportCloses;

setupTableExports();
setupChartExports();
