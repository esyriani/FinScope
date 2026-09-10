const DOWNLOAD_URL_REVOKE_DELAY_MS = 1000;

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
    window.setTimeout(() => URL.revokeObjectURL(url), DOWNLOAD_URL_REVOKE_DELAY_MS);
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

function beginTableExport(button) {
    if (button?.dataset.tableExportBusy === "true") {
        return false;
    }

    if (button) {
        button.dataset.tableExportBusy = "true";
        button.setAttribute("aria-busy", "true");
    }

    return true;
}

function finishTableExport(button) {
    if (!button) {
        return;
    }

    button.removeAttribute("aria-busy");
    delete button.dataset.tableExportBusy;
}

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

function showModalAfterExpandedExportCloses(modalElement, relatedTarget) {
    if (!window.bootstrap?.Modal) return;

    closeExpandedExportModal().then(() => {
        bootstrap.Modal.getOrCreateInstance(modalElement).show(relatedTarget);
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

function elementExportText(element, options = {}) {
    if (element.matches("[data-row-action]")) {
        return "";
    }

    const clone = element.cloneNode(true);
    const sourceControls = Array.from(element.querySelectorAll("select, textarea, input"));
    const clonedControls = Array.from(clone.querySelectorAll("select, textarea, input"));

    clonedControls.forEach((control, index) => {
        control.replaceWith(document.createTextNode(formControlExportValue(sourceControls[index] || control)));
    });

    clone.querySelectorAll("[data-row-action], svg, script, style").forEach((node) => node.remove());
    if (options.keepButtonText) {
        clone.querySelectorAll("button").forEach((node) => {
            node.replaceWith(document.createTextNode(normalizeExportText(node.textContent)));
        });
    } else {
        clone.querySelectorAll("button").forEach((node) => node.remove());
    }
    return normalizeExportText(clone.textContent);
}

function cellExportText(cell) {
    return elementExportText(cell, { keepButtonText: cell.matches("th") });
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

function tableRowsForExport(table) {
    const rows = Array.from(table.querySelectorAll("tr"));
    const visibleSourceIds = visibleExportSourceIds(table);
    return rows.filter((row) => {
        if (isExportHeaderRow(row)) return true;
        if (visibleSourceIds) return visibleSourceIds.has(rowExportId(row));
        return isDisplayedExportRow(row);
    });
}

function tableMatrix(table) {
    const columnPlan = tableExportColumnPlan(table);
    const headers = tableHeaderNames(table, columnPlan);
    const headerRow = table.tHead?.rows[0] || table.querySelector("tr");

    return tableRowsForExport(table)
        .map((row) => {
            if (row === headerRow) {
                return headers;
            }

            const cells = rowCellsExpanded(row);
            return columnPlan.map((column) => cellExportPartText(cells[column.sourceIndex] || null, column.partLabel));
        })
        .filter((row) => row.some((value) => value !== ""));
}

function notifyTableExportError(error) {
    console.error(error);
    window.alert?.(financeTranslate("The table could not be exported."));
}

function exportTableCsv(table, filenameBase, button = null) {
    if (!beginTableExport(button)) return;

    try {
        const csv = tableMatrix(table)
            .map((row) => row.map(csvEscape).join(","))
            .join("\r\n");

        downloadBlob(new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" }), `${filenameBase}.csv`);
    } catch (error) {
        notifyTableExportError(error);
    } finally {
        finishTableExport(button);
    }
}

const ACTION_HEADER_RE = /^actions?$/i;
const EXPORT_PART_SELECTOR =
    "[data-export-part], [data-export-label], [data-export-header], [data-export-text], [data-export-value], [data-export-type]";

function rowCellsExpanded(row) {
    const cells = [];
    Array.from(row?.cells || []).forEach((cell) => {
        const colspan = Math.max(1, Number(cell.colSpan || 1) || 1);
        cells.push(cell);
        for (let index = 1; index < colspan; index += 1) {
            cells.push(null);
        }
    });
    return cells;
}

function cellExportParts(cell) {
    if (!cell || isActionExportCell(cell)) {
        return [];
    }

    const partElements = [
        ...(cell.matches(EXPORT_PART_SELECTOR) ? [cell] : []),
        ...Array.from(cell.querySelectorAll(EXPORT_PART_SELECTOR)),
    ];
    if (!partElements.length) {
        return [
            {
                header: cell.dataset.exportHeader || "",
                explicitType: cell.dataset.exportType || "",
                explicitValue: cell.getAttribute("data-export-value") ?? "",
                label: normalizeExportText(cell.dataset.exportLabel || ""),
                text: cell.hasAttribute("data-export-text")
                    ? normalizeExportText(cell.getAttribute("data-export-text") || "")
                    : cellExportText(cell),
            },
        ];
    }

    return partElements.map((part) => ({
        header: normalizeExportText(part.dataset.exportHeader || ""),
        explicitType: part.dataset.exportType || "",
        explicitValue: part.getAttribute("data-export-value") ?? "",
        label: normalizeExportText(part.dataset.exportLabel || ""),
        text: part.hasAttribute("data-export-text")
            ? normalizeExportText(part.getAttribute("data-export-text") || "")
            : elementExportText(part),
    }));
}

function cellExportPart(cell, partLabel) {
    const parts = cellExportParts(cell);
    const exact = parts.find((part) => part.label === partLabel);
    if (exact) {
        return exact;
    }

    if (!partLabel && parts.length === 1) {
        return parts[0];
    }

    return {
        explicitType: "",
        explicitValue: "",
        header: "",
        label: partLabel,
        text: "",
    };
}

function cellExportPartText(cell, partLabel) {
    const part = cellExportPart(cell, partLabel);
    return part.text || part.explicitValue;
}

function isActionExportCell(cell) {
    return Boolean(cell?.matches("[data-row-action]") || cell?.querySelector("[data-row-action]"));
}

function isActionExportColumn(columnIndex, expandedRows) {
    const cells = expandedRows.map((row) => row[columnIndex]).filter(Boolean);
    const headerText = normalizeExportText(
        cells
            .filter((cell) => cell.closest("thead"))
            .map(cellExportText)
            .join(" ")
    );
    const hasActionBodyCell = cells.some((cell) => !cell.closest("thead") && isActionExportCell(cell));

    return ACTION_HEADER_RE.test(headerText) || hasActionBodyCell;
}

function tableExportColumnPlan(table) {
    const rows = tableRowsForExport(table);
    const expandedRows = rows.map(rowCellsExpanded);
    const columnCount = Math.max(1, ...expandedRows.map((row) => row.length));

    return Array.from({ length: columnCount }, (_value, sourceIndex) => sourceIndex)
        .filter((sourceIndex) => !isActionExportColumn(sourceIndex, expandedRows))
        .flatMap((sourceIndex) => {
            const parts = [];
            expandedRows.forEach((row) => {
                const cell = row[sourceIndex];
                if (!cell || cell.closest("thead, tfoot")) {
                    return;
                }
                cellExportParts(cell).forEach((part) => {
                    if (
                        !parts.some(
                            (knownPart) => knownPart.partLabel === part.label && knownPart.header === part.header
                        )
                    ) {
                        parts.push({ header: part.header, partLabel: part.label });
                    }
                });
            });

            if (!parts.length) {
                parts.push({ header: "", partLabel: "" });
            }

            return parts.map((part) => ({ ...part, sourceIndex }));
        });
}

function exportHeaderName(baseHeader, column) {
    if (column.header) {
        return column.header;
    }
    if (!column.partLabel) {
        return baseHeader;
    }

    return normalizeExportText(`${baseHeader} ${column.partLabel}`);
}

function tableHeaderNames(table, columnPlan) {
    const headerRow = table.tHead?.rows[0] || table.querySelector("tr");
    if (!headerRow) {
        return ["Column1"];
    }

    const cells = rowCellsExpanded(headerRow);
    return columnPlan.map((column) =>
        exportHeaderName(cells[column.sourceIndex] ? cellExportText(cells[column.sourceIndex]) : "", column)
    );
}

function tableColumnSortTypes(table) {
    const sortTypes = new Map();
    table.querySelectorAll("[data-sort-column]").forEach((control) => {
        const column = Number(control.dataset.sortColumn);
        if (Number.isInteger(column) && column >= 0) {
            sortTypes.set(column, control.dataset.sortType || "text");
        }
    });
    return sortTypes;
}

function tableBodyRowsForExcel(table) {
    return tableRowsForExport(table).filter((row) => row.closest("tbody") && !row.hasAttribute("data-sort-ignore"));
}

function rowExportMetadata(row, columnPlan, sortTypes) {
    const cells = rowCellsExpanded(row);
    return columnPlan.map((column) => {
        const part = cellExportPart(cells[column.sourceIndex] || null, column.partLabel);
        return {
            ...part,
            explicitValue:
                part.explicitValue ||
                (sortTypes.get(column.sourceIndex) === "number" && !column.partLabel
                    ? cells[column.sourceIndex]?.getAttribute("data-sort-value") || ""
                    : ""),
        };
    });
}

function buildTableExportWorkbookSource(table) {
    const columnPlan = tableExportColumnPlan(table);
    const sortTypes = tableColumnSortTypes(table);

    return {
        headers: tableHeaderNames(table, columnPlan),
        rows: tableBodyRowsForExcel(table).map((row) => rowExportMetadata(row, columnPlan, sortTypes)),
        sortTypes: columnPlan.map((column) => sortTypes.get(column.sourceIndex) || ""),
        totalLabel: financeTranslate("Total"),
    };
}

function createTableExportXlsxBlob(source, sheetName) {
    const writer = window.financeXlsxWriter;
    if (!writer?.createXlsxBlob) {
        throw new Error("XLSX export writer is not loaded.");
    }

    return writer.createXlsxBlob(source, sheetName);
}

function exportTableExcel(table, filenameBase, sheetName, button = null) {
    if (!beginTableExport(button)) return;

    try {
        downloadBlob(
            createTableExportXlsxBlob(buildTableExportWorkbookSource(table), sheetName),
            `${filenameBase}.xlsx`
        );
    } catch (error) {
        notifyTableExportError(error);
    } finally {
        finishTableExport(button);
    }
}

function insertTableExportToolbar(table, toolbar) {
    const toolbarTable = tableVisibleSource(table) || table;
    const card = toolbarTable.closest(".card");
    const exportScope = toolbarTable.closest("[data-table-export-scope]");
    const localToolbarTarget = exportScope?.querySelector("[data-table-export-toolbar]");
    const localTitleBar = exportScope?.querySelector(".section-title");
    const titleBar = localTitleBar || card?.querySelector(".section-title");
    const cardHeader = card?.querySelector(".card-header");
    const tableResponsive = toolbarTable.closest(".table-responsive");

    if (localToolbarTarget) {
        localToolbarTarget.appendChild(toolbar);
        return;
    }

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
            createExportButton(financeTranslate("CSV"), "download", (event) =>
                exportTableCsv(table, filenameBase, event.currentTarget)
            )
        );
        toolbar.appendChild(
            createExportButton(financeTranslate("Excel"), "download", (event) =>
                exportTableExcel(table, filenameBase, title, event.currentTarget)
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
