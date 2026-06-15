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

function tableExportRoot(table) {
    const sourceTable = tableVisibleSource(table) || table;
    return (
        sourceTable.closest("[data-table-export-scope]") ||
        sourceTable.closest(".card") ||
        sourceTable.closest(".modal-content") ||
        document
    );
}

function clientPaginatedTablePageCount(table) {
    const sourceTable = tableVisibleSource(table) || table;
    if (!sourceTable.matches("[data-paginated-table]")) {
        return 1;
    }

    const pageSize = Math.max(1, Number(sourceTable.dataset.pageSize || 25) || 25);
    const rows = Array.from(sourceTable.tBodies[0]?.rows || []).filter((row) => !row.hasAttribute("data-sort-ignore"));
    return Math.max(1, Math.ceil(rows.length / pageSize));
}

function numericPaginationLinks(root) {
    return Array.from(root.querySelectorAll("nav .pagination a.page-link[href]"))
        .map((link) => {
            const pageNumber = Number(normalizeExportText(link.textContent));
            if (!Number.isInteger(pageNumber) || pageNumber <= 0) {
                return null;
            }

            const url = new URL(link.href, window.location.href);
            if (url.origin !== window.location.origin) {
                return null;
            }

            return { pageNumber, url };
        })
        .filter(Boolean);
}

function activePaginationPage(root) {
    const activeLink = root.querySelector("nav .pagination .page-item.active .page-link");
    const pageNumber = Number(normalizeExportText(activeLink?.textContent || ""));
    return Number.isInteger(pageNumber) && pageNumber > 0 ? pageNumber : 1;
}

function inferPaginationPageParameter(pageLinks) {
    const scores = new Map();
    pageLinks.forEach(({ pageNumber, url }) => {
        url.searchParams.forEach((value, name) => {
            if (Number(value) === pageNumber) {
                scores.set(name, (scores.get(name) || 0) + 1);
            }
        });
    });

    return (
        Array.from(scores.entries()).sort((left, right) => {
            if (right[1] !== left[1]) return right[1] - left[1];
            if (left[0] === "page") return -1;
            if (right[0] === "page") return 1;
            return left[0].localeCompare(right[0]);
        })[0]?.[0] || ""
    );
}

function serverPaginationPlan(table) {
    const root = tableExportRoot(table);
    const pageLinks = numericPaginationLinks(root);
    if (!pageLinks.length) {
        return null;
    }

    const pageNumbers = pageLinks.map((link) => link.pageNumber);
    const totalPages = Math.max(...pageNumbers);
    if (totalPages <= 1) {
        return null;
    }

    const pageParameter = inferPaginationPageParameter(pageLinks);
    const template = pageLinks.find((link) => link.pageNumber === totalPages) || pageLinks[0];
    if (!pageParameter || !template) {
        return null;
    }

    return {
        activePage: Math.min(totalPages, activePaginationPage(root)),
        pageUrls: Array.from({ length: totalPages }, (_value, index) => {
            const pageNumber = index + 1;
            const url = new URL(template.url.href);
            url.searchParams.set(pageParameter, String(pageNumber));
            return { pageNumber, url: url.href };
        }),
    };
}

function tableHasMultipleExportPages(table) {
    return clientPaginatedTablePageCount(table) > 1 || Boolean(serverPaginationPlan(table));
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

function exportableTablesIn(root) {
    return Array.from(root.querySelectorAll("table")).filter((table) => !table.hasAttribute("data-no-export"));
}

function matchingTableInDocument(documentRoot, sourceTable, sourceIndex) {
    if (sourceTable.id) {
        return documentRoot.getElementById(sourceTable.id);
    }

    return exportableTablesIn(documentRoot)[sourceIndex] || null;
}

async function fetchExportTablePage(url, sourceTable, sourceIndex) {
    const response = await fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "text/html" },
    });
    if (!response.ok) {
        throw new Error(`Table export page request failed: ${response.status}`);
    }

    const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
    const table = matchingTableInDocument(parsed, sourceTable, sourceIndex);
    if (!table) {
        throw new Error("Table export page did not contain the expected table.");
    }
    return table;
}

async function tableExportTablesForScope(table, scope) {
    if (scope !== "all") {
        return [table];
    }

    const plan = serverPaginationPlan(table);
    if (!plan) {
        return [table];
    }

    const sourceIndex = exportableTablesIn(document).indexOf(table);
    if (sourceIndex < 0) {
        return [table];
    }

    return Promise.all(
        plan.pageUrls.map(({ pageNumber, url }) =>
            pageNumber === plan.activePage ? Promise.resolve(table) : fetchExportTablePage(url, table, sourceIndex)
        )
    );
}

function tableRowsForExportTables(primaryTable, tables, scope) {
    if (tables.length <= 1) {
        return tableRowsForExport(primaryTable, scope);
    }

    return [
        ...Array.from(primaryTable.tHead?.rows || []),
        ...tables.flatMap((table) => Array.from(table.tBodies).flatMap((body) => Array.from(body.rows))),
        ...Array.from(primaryTable.tFoot?.rows || []),
    ];
}

function tableMatrix(table, scope = "all", exportTables = [table]) {
    const columnPlan = tableExportColumnPlan(table, scope, exportTables);
    const headers = tableHeaderNames(table, columnPlan);
    const headerRow = table.tHead?.rows[0] || table.querySelector("tr");

    return tableRowsForExportTables(table, exportTables, scope)
        .map((row) => {
            if (row === headerRow) {
                return headers;
            }

            const cells = rowCellsExpanded(row);
            return columnPlan.map((column) => cellExportPartText(cells[column.sourceIndex] || null, column.partLabel));
        })
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

async function tableExportScope(table) {
    if (!tableHasMultipleExportPages(table)) {
        return "all";
    }

    return chooseTableExportScope();
}

function notifyTableExportError(error) {
    console.error(error);
    window.alert?.(financeTranslate("Could not load every table page for export."));
}

async function exportTableCsv(table, filenameBase) {
    const scope = await tableExportScope(table);
    if (!scope) return;

    try {
        const exportTables = await tableExportTablesForScope(table, scope);
        const csv = tableMatrix(table, scope, exportTables)
            .map((row) => row.map(csvEscape).join(","))
            .join("\r\n");

        downloadBlob(new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" }), `${filenameBase}.csv`);
    } catch (error) {
        notifyTableExportError(error);
    }
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

const XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const XLSX_MONEY_FORMAT = '#,##0.00 [$$-C0C];-#,##0.00 [$$-C0C];"-" [$$-C0C]';
const XLSX_NUMBER_FORMAT = "#,##0.00";
const XLSX_MAX_TABLE_HEADER_LENGTH = 255;
const XLSX_TABLE_NAME = "Table1";
const ACTION_HEADER_RE = /^actions?$/i;
const MONEY_HEADER_RE =
    /\b(amount|spending|income|balance|debit|credit|payment|paid|current|prior|change|delta|expected|actual|budget)\b|\$/i;
const PERCENT_HEADER_RE = /\b(percent|percentage|share|rate)\b|%/i;
const CURRENCY_RE = /[$\u20ac\u00a3\u00a5]/;
const PERCENT_RE = /%/;
const STRICT_NUMBER_RE = /^[+-]?(?:(?:\d+)|(?:\d{1,3}(?:[ ,]\d{3})+))(?:[.,]\d+)?(?:e[+-]?\d+)?$/i;
const NUMBER_TOKEN_RE = /[+-]?\(?\d[\d ,.]*\)?/;
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

function uniqueExcelHeaders(values) {
    const used = new Map();

    return values.map((value, index) => {
        const rawBase = normalizeExportText(value) || `Column${index + 1}`;
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

function tableExportColumnPlan(table, scope, exportTables = [table]) {
    const rows = tableRowsForExportTables(table, exportTables, scope);
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
    return uniqueExcelHeaders(
        columnPlan.map((column) =>
            exportHeaderName(cells[column.sourceIndex] ? cellExportText(cells[column.sourceIndex]) : "", column)
        )
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

function tableBodyRowsForExcel(table, scope, exportTables = [table]) {
    return tableRowsForExportTables(table, exportTables, scope).filter(
        (row) => row.closest("tbody") && !row.hasAttribute("data-sort-ignore")
    );
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

function isBlankExcelValue(value) {
    return normalizeExportText(value) === "";
}

function normalizeNumberToken(value) {
    const raw = normalizeExportText(value)
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

function parseWholeNumberText(value) {
    const text = normalizeExportText(value);
    if (!STRICT_NUMBER_RE.test(text.replace(/\u00a0/g, " "))) {
        return null;
    }
    return normalizeNumberToken(text);
}

function parseFirstNumberText(value) {
    const match = normalizeExportText(value).match(NUMBER_TOKEN_RE);
    return match ? normalizeNumberToken(match[0]) : null;
}

function parsedExcelValue(cell, headerName, sortType) {
    const explicitValue = normalizeExportText(cell.explicitValue);
    const text = normalizeExportText(cell.text);
    const source = explicitValue || text;

    if (isBlankExcelValue(source)) {
        return { kind: "blank", value: null };
    }

    const explicitKind = cell.explicitType;
    const looksPercent = explicitKind === "percent" || PERCENT_RE.test(text) || PERCENT_HEADER_RE.test(headerName);
    const looksMoney = explicitKind === "money" || CURRENCY_RE.test(text) || MONEY_HEADER_RE.test(headerName);

    if (explicitValue) {
        const explicitNumber = normalizeNumberToken(explicitValue);
        if (explicitNumber !== null) {
            return {
                kind: explicitKind || (looksPercent ? "percent" : looksMoney ? "money" : "number"),
                value: looksPercent && PERCENT_RE.test(explicitValue) ? explicitNumber / 100 : explicitNumber,
            };
        }
    }

    if (looksPercent) {
        const percentNumber = parseFirstNumberText(text);
        if (percentNumber !== null && PERCENT_RE.test(text)) {
            return { kind: "percent", value: percentNumber / 100 };
        }
    }

    if (looksMoney) {
        const moneyNumber = CURRENCY_RE.test(text) ? parseFirstNumberText(text) : parseWholeNumberText(text);
        if (moneyNumber !== null) {
            return { kind: "money", value: moneyNumber };
        }
    }

    const strictNumber = sortType === "number" ? parseFirstNumberText(text) : parseWholeNumberText(text);
    if (strictNumber !== null) {
        return { kind: "number", value: strictNumber };
    }

    return { kind: "string", value: text };
}

function analyzeExcelColumns(headers, rows, parsedRows, sortTypes) {
    return headers.map((header, columnIndex) => {
        const parsedCells = parsedRows.map((row) => row[columnIndex]);
        const nonBlankCells = parsedCells.filter((cell) => cell.kind !== "blank");
        const numeric = nonBlankCells.length > 0 && nonBlankCells.every((cell) => cell.kind !== "string");
        const hasMoneyCells = nonBlankCells.some((cell) => cell.kind === "money");
        const hasPercentCells = nonBlankCells.some((cell) => cell.kind === "percent");
        const percent = numeric && !hasMoneyCells && (hasPercentCells || PERCENT_HEADER_RE.test(header));
        const money = numeric && !percent && (hasMoneyCells || MONEY_HEADER_RE.test(header));
        const type = money ? "money" : percent ? "percent" : numeric ? "number" : "string";
        const sortType = sortTypes[columnIndex] || "";
        const widthSamples = [
            header,
            ...rows.map((row) => row[columnIndex]?.text || row[columnIndex]?.explicitValue || ""),
        ];
        const maxLength = Math.max(8, ...widthSamples.map((value) => normalizeExportText(value).length));

        return {
            type,
            sortType,
            total: numeric,
            width: Math.min(60, Math.max(8, maxLength + 2)),
        };
    });
}

function buildExcelTableModel(table, scope, exportTables = [table]) {
    const columnPlan = tableExportColumnPlan(table, scope, exportTables);
    const headers = tableHeaderNames(table, columnPlan);
    const sortTypes = tableColumnSortTypes(table);
    const exportSortTypes = columnPlan.map((column) => sortTypes.get(column.sourceIndex) || "");
    const rows = tableBodyRowsForExcel(table, scope, exportTables)
        .map((row) => rowExportMetadata(row, columnPlan, sortTypes))
        .filter((row) => row.some((cell) => !isBlankExcelValue(cell.text) || !isBlankExcelValue(cell.explicitValue)));
    const parsedRows = rows.map((row) =>
        row.map((cell, columnIndex) => parsedExcelValue(cell, headers[columnIndex], exportSortTypes[columnIndex]))
    );
    const columns = analyzeExcelColumns(headers, rows, parsedRows, exportSortTypes);
    const hasTotalRow = columns.some((column) => column.total);
    const labelColumnIndex = columns.findIndex((column) => !column.total);

    return {
        columns,
        hasTotalRow,
        headers,
        labelColumnIndex,
        parsedRows,
        rows,
    };
}

function excelColumnName(index) {
    let name = "";
    let value = index + 1;
    while (value > 0) {
        const remainder = (value - 1) % 26;
        name = String.fromCharCode(65 + remainder) + name;
        value = Math.floor((value - 1) / 26);
    }
    return name;
}

function excelCellReference(columnIndex, rowIndex) {
    return `${excelColumnName(columnIndex)}${rowIndex}`;
}

function excelRange(columnCount, rowCount) {
    return `A1:${excelColumnName(columnCount - 1)}${rowCount}`;
}

function excelStyleIdForType(type) {
    if (type === "money") return 1;
    if (type === "percent") return 2;
    if (type === "number") return 3;
    return 0;
}

function excelNumberText(value) {
    if (!Number.isFinite(value)) {
        return "0";
    }
    return String(Math.round((value + Number.EPSILON) * 1000000000000) / 1000000000000);
}

function excelStringCell(reference, value) {
    const text = xmlEscape(value);
    return `<c r="${reference}" t="inlineStr"><is><t>${text}</t></is></c>`;
}

function excelNumberCell(reference, value, styleId, formula = "") {
    const style = styleId ? ` s="${styleId}"` : "";
    const formulaXml = formula ? `<f>${xmlEscape(formula)}</f>` : "";
    return `<c r="${reference}"${style}>${formulaXml}<v>${excelNumberText(value)}</v></c>`;
}

function excelWorksheetRows(model) {
    const rows = [
        `<row r="1" spans="1:${model.headers.length}">${model.headers
            .map((header, columnIndex) => excelStringCell(excelCellReference(columnIndex, 1), header))
            .join("")}</row>`,
    ];

    model.rows.forEach((row, rowIndex) => {
        const excelRowIndex = rowIndex + 2;
        const cells = row
            .map((cell, columnIndex) => {
                const reference = excelCellReference(columnIndex, excelRowIndex);
                const parsed = model.parsedRows[rowIndex][columnIndex];
                const column = model.columns[columnIndex];

                if (parsed.kind === "blank") {
                    return "";
                }
                if (column.total && parsed.kind !== "string") {
                    return excelNumberCell(reference, parsed.value, excelStyleIdForType(column.type));
                }
                return excelStringCell(reference, cell.text);
            })
            .join("");
        rows.push(`<row r="${excelRowIndex}" spans="1:${model.headers.length}">${cells}</row>`);
    });

    if (model.hasTotalRow) {
        const totalRowIndex = model.rows.length + 2;
        const firstDataRow = 2;
        const lastDataRow = model.rows.length + 1;
        const totalLabel = typeof financeTranslate === "function" ? financeTranslate("Total") : "Total";
        const cells = model.columns
            .map((column, columnIndex) => {
                const reference = excelCellReference(columnIndex, totalRowIndex);
                if (column.total) {
                    const sum = model.parsedRows.reduce((total, row) => total + (row[columnIndex].value || 0), 0);
                    const columnName = excelColumnName(columnIndex);
                    const formula = `SUBTOTAL(109,${columnName}${firstDataRow}:${columnName}${lastDataRow})`;
                    return excelNumberCell(reference, sum, excelStyleIdForType(column.type), formula);
                }
                if (columnIndex === model.labelColumnIndex) {
                    return excelStringCell(reference, totalLabel);
                }
                return "";
            })
            .join("");
        rows.push(`<row r="${totalRowIndex}" spans="1:${model.headers.length}">${cells}</row>`);
    }

    return rows.join("");
}

function excelWorksheetXml(model) {
    const dataEndRow = model.rows.length + 1;
    const totalRowCount = model.hasTotalRow ? 1 : 0;
    const rowCount = dataEndRow + totalRowCount;
    const dimension = excelRange(model.headers.length, rowCount);
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
    <sheetData>${excelWorksheetRows(model)}</sheetData>
    <tableParts count="1"><tablePart r:id="rId1"/></tableParts>
</worksheet>`;
}

function excelTableXml(model) {
    const dataEndRow = model.rows.length + 1;
    const rowCount = dataEndRow + (model.hasTotalRow ? 1 : 0);
    const tableRef = excelRange(model.headers.length, rowCount);
    const autoFilterRef = excelRange(model.headers.length, dataEndRow);
    const totalAttrs = model.hasTotalRow ? 'totalsRowCount="1"' : 'totalsRowShown="0"';
    const totalLabel = typeof financeTranslate === "function" ? financeTranslate("Total") : "Total";
    const columns = model.headers
        .map((header, index) => {
            const column = model.columns[index];
            const attrs = [`id="${index + 1}"`, `name="${xmlEscape(header)}"`];
            if (model.hasTotalRow && column.total) {
                attrs.push('totalsRowFunction="sum"');
            } else if (model.hasTotalRow && index === model.labelColumnIndex) {
                attrs.push(`totalsRowLabel="${xmlEscape(totalLabel)}"`);
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

function excelStylesXml() {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
    <numFmts count="2">
        <numFmt numFmtId="164" formatCode="${xmlEscape(XLSX_MONEY_FORMAT)}"/>
        <numFmt numFmtId="165" formatCode="${xmlEscape(XLSX_NUMBER_FORMAT)}"/>
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

function excelWorkbookXml(sheetName) {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <bookViews><workbookView/></bookViews>
    <sheets><sheet name="${xmlEscape(excelSheetName(sheetName))}" sheetId="1" r:id="rId1"/></sheets>
</workbook>`;
}

function excelWorkbookRelationshipsXml() {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
    <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`;
}

function excelWorksheetRelationshipsXml() {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/table" Target="../tables/table1.xml"/>
</Relationships>`;
}

function excelPackageRelationshipsXml() {
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>`;
}

function excelContentTypesXml() {
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
        { name: "[Content_Types].xml", content: excelContentTypesXml() },
        { name: "_rels/.rels", content: excelPackageRelationshipsXml() },
        { name: "xl/workbook.xml", content: excelWorkbookXml(sheetName) },
        { name: "xl/_rels/workbook.xml.rels", content: excelWorkbookRelationshipsXml() },
        { name: "xl/worksheets/sheet1.xml", content: excelWorksheetXml(model) },
        { name: "xl/worksheets/_rels/sheet1.xml.rels", content: excelWorksheetRelationshipsXml() },
        { name: "xl/tables/table1.xml", content: excelTableXml(model) },
        { name: "xl/styles.xml", content: excelStylesXml() },
    ];
}

const CRC32_TABLE = Array.from({ length: 256 }, (_value, index) => {
    let crc = index;
    for (let bit = 0; bit < 8; bit += 1) {
        crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
    }
    return crc >>> 0;
});

function crc32(bytes) {
    let crc = 0xffffffff;
    bytes.forEach((byte) => {
        crc = CRC32_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    });
    return (crc ^ 0xffffffff) >>> 0;
}

function writeUint16(target, offset, value) {
    target[offset] = value & 0xff;
    target[offset + 1] = (value >>> 8) & 0xff;
}

function writeUint32(target, offset, value) {
    target[offset] = value & 0xff;
    target[offset + 1] = (value >>> 8) & 0xff;
    target[offset + 2] = (value >>> 16) & 0xff;
    target[offset + 3] = (value >>> 24) & 0xff;
}

function zipDateTime(date = new Date()) {
    const year = Math.min(2107, Math.max(1980, date.getFullYear()));
    return {
        date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
        time: (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2),
    };
}

function createZipBlob(files, mimeType) {
    const encoder = new TextEncoder();
    const timestamp = zipDateTime();
    const localParts = [];
    const centralParts = [];
    let offset = 0;

    files.forEach((file) => {
        const nameBytes = encoder.encode(file.name);
        const contentBytes = typeof file.content === "string" ? encoder.encode(file.content) : file.content;
        const checksum = crc32(contentBytes);
        const localHeader = new Uint8Array(30 + nameBytes.length);
        writeUint32(localHeader, 0, 0x04034b50);
        writeUint16(localHeader, 4, 20);
        writeUint16(localHeader, 6, 0x0800);
        writeUint16(localHeader, 8, 0);
        writeUint16(localHeader, 10, timestamp.time);
        writeUint16(localHeader, 12, timestamp.date);
        writeUint32(localHeader, 14, checksum);
        writeUint32(localHeader, 18, contentBytes.length);
        writeUint32(localHeader, 22, contentBytes.length);
        writeUint16(localHeader, 26, nameBytes.length);
        writeUint16(localHeader, 28, 0);
        localHeader.set(nameBytes, 30);
        localParts.push(localHeader, contentBytes);

        const centralHeader = new Uint8Array(46 + nameBytes.length);
        writeUint32(centralHeader, 0, 0x02014b50);
        writeUint16(centralHeader, 4, 20);
        writeUint16(centralHeader, 6, 20);
        writeUint16(centralHeader, 8, 0x0800);
        writeUint16(centralHeader, 10, 0);
        writeUint16(centralHeader, 12, timestamp.time);
        writeUint16(centralHeader, 14, timestamp.date);
        writeUint32(centralHeader, 16, checksum);
        writeUint32(centralHeader, 20, contentBytes.length);
        writeUint32(centralHeader, 24, contentBytes.length);
        writeUint16(centralHeader, 28, nameBytes.length);
        writeUint16(centralHeader, 30, 0);
        writeUint16(centralHeader, 32, 0);
        writeUint16(centralHeader, 34, 0);
        writeUint16(centralHeader, 36, 0);
        writeUint32(centralHeader, 38, 0);
        writeUint32(centralHeader, 42, offset);
        centralHeader.set(nameBytes, 46);
        centralParts.push(centralHeader);

        offset += localHeader.length + contentBytes.length;
    });

    const centralDirectoryOffset = offset;
    const centralDirectorySize = centralParts.reduce((total, part) => total + part.length, 0);
    const endRecord = new Uint8Array(22);
    writeUint32(endRecord, 0, 0x06054b50);
    writeUint16(endRecord, 4, 0);
    writeUint16(endRecord, 6, 0);
    writeUint16(endRecord, 8, files.length);
    writeUint16(endRecord, 10, files.length);
    writeUint32(endRecord, 12, centralDirectorySize);
    writeUint32(endRecord, 16, centralDirectoryOffset);
    writeUint16(endRecord, 20, 0);

    return new Blob([...localParts, ...centralParts, endRecord], { type: mimeType });
}

function createXlsxBlob(model, sheetName) {
    return createZipBlob(xlsxPackageFiles(model, sheetName), XLSX_MIME_TYPE);
}

async function exportTableExcel(table, filenameBase, sheetName) {
    const scope = await tableExportScope(table);
    if (!scope) return;

    try {
        const exportTables = await tableExportTablesForScope(table, scope);
        downloadBlob(
            createXlsxBlob(buildExcelTableModel(table, scope, exportTables), sheetName),
            `${filenameBase}.xlsx`
        );
    } catch (error) {
        notifyTableExportError(error);
    }
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
