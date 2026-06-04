function setupTableRowInteractions(root = document) {
    const interactiveSelector = "a, button, input, select, textarea, form, [data-row-action]";

    root.querySelectorAll("table:not([data-no-row-select]) tbody tr").forEach((row) => {
        if (row.dataset.tableRowInteractionReady === "true") {
            return;
        }

        row.dataset.tableRowInteractionReady = "true";

        row.addEventListener("click", (event) => {
            if (event.target.closest(interactiveSelector)) {
                return;
            }

            const rowHref = row.getAttribute("data-row-href");
            if (rowHref) {
                if (row.dataset.rowDrilldown === "dblclick") {
                    selectDashboardDrilldownItem(row);
                    return;
                }

                window.location.href = rowHref;
                return;
            }

            const table = row.closest("table");
            table?.querySelectorAll("tbody tr.table-row-selected").forEach((selectedRow) => {
                selectedRow.classList.remove("table-row-selected");
            });
            row.classList.add("table-row-selected");
        });

        row.addEventListener("dblclick", (event) => {
            if (event.target.closest(interactiveSelector)) {
                return;
            }

            const rowHref = row.getAttribute("data-row-href");
            if (rowHref && row.dataset.rowDrilldown === "dblclick") {
                window.showBusyOverlayForElement?.(row);
                window.location.href = rowHref;
                return;
            }

            const targetSelector = row.getAttribute("data-row-edit-target");
            const modalElement = targetSelector ? document.querySelector(targetSelector) : null;
            if (!modalElement || !window.bootstrap?.Modal) {
                return;
            }

            window.bootstrap.Modal.getOrCreateInstance(modalElement).show();
        });
    });
}

function setupTransactionBatchActions(root = document) {
    root.querySelectorAll("[data-transaction-batch-table]").forEach((table) => {
        if (table.dataset.transactionBatchReady === "true") {
            return;
        }

        table.dataset.transactionBatchReady = "true";
        const container = table.closest(".card") || root;
        const bar = container.querySelector("[data-transaction-batch-bar]");
        const form = container.querySelector("[data-transaction-batch-form]");
        const countLabel = container.querySelector("[data-transaction-batch-count-label]");
        const inputContainer = container.querySelector("[data-transaction-batch-inputs]");
        const selectAll = table.querySelector("[data-transaction-select-all]");
        const rowCheckboxes = Array.from(table.querySelectorAll("[data-transaction-row-checkbox]"));
        const allIds = transactionBatchIds(table, rowCheckboxes);
        const selectedIds = new Set();

        if (!bar || !form || !inputContainer || !selectAll || rowCheckboxes.length === 0) {
            return;
        }

        function syncHiddenInputs() {
            inputContainer.replaceChildren(
                ...Array.from(selectedIds).map((transactionId) => {
                    const input = document.createElement("input");
                    input.type = "hidden";
                    input.name = "transaction_ids";
                    input.value = transactionId;
                    return input;
                })
            );
        }

        function syncSelectionState() {
            const selectedCount = selectedIds.size;
            rowCheckboxes.forEach((checkbox) => {
                checkbox.checked = selectedIds.has(String(checkbox.value));
            });

            selectAll.checked = allIds.length > 0 && selectedCount === allIds.length;
            selectAll.indeterminate = selectedCount > 0 && selectedCount < allIds.length;
            bar.hidden = selectedCount === 0;

            if (countLabel) {
                countLabel.textContent = financeTranslate(
                    "{count} selected",
                    { count: selectedCount }
                );
            }
            syncHiddenInputs();
        }

        selectAll.addEventListener("change", () => {
            selectedIds.clear();
            if (selectAll.checked) {
                allIds.forEach((transactionId) => selectedIds.add(transactionId));
            }
            syncSelectionState();
        });

        rowCheckboxes.forEach((checkbox) => {
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) {
                    selectedIds.add(String(checkbox.value));
                } else {
                    selectedIds.delete(String(checkbox.value));
                }
                syncSelectionState();
            });
        });

        form.addEventListener("submit", syncHiddenInputs);
        syncSelectionState();
    });
}

function transactionBatchIds(table, rowCheckboxes) {
    try {
        const parsed = JSON.parse(table.dataset.allTransactionIds || "[]");
        if (Array.isArray(parsed)) {
            return parsed.map((transactionId) => String(transactionId));
        }
    } catch (_error) {
        // Fall back to visible row checkboxes when the server-provided list is unavailable.
    }

    return rowCheckboxes.map((checkbox) => String(checkbox.value));
}

function setupCollapseToggleLabels(root = document) {
    root.querySelectorAll("[data-collapse-label-toggle]").forEach((button) => {
        if (button.dataset.collapseLabelReady === "true") {
            return;
        }

        button.dataset.collapseLabelReady = "true";
        const targetSelector = button.getAttribute("data-bs-target") || button.getAttribute("href");
        const target = targetSelector ? document.querySelector(targetSelector) : null;
        const icon = button.querySelector("[data-collapse-toggle-icon]");
        const label = button.querySelector("[data-collapse-toggle-label]");
        const showLabel = button.dataset.showLabel || "Show table";
        const hideLabel = button.dataset.hideLabel || "Hide table";

        function setExpanded(expanded) {
            button.setAttribute("aria-expanded", expanded ? "true" : "false");
            if (label) {
                label.textContent = expanded ? hideLabel : showLabel;
            }
            if (icon) {
                icon.classList.toggle("bi-chevron-down", !expanded);
                icon.classList.toggle("bi-chevron-up", expanded);
            }
        }

        setExpanded(target?.classList.contains("show") || button.getAttribute("aria-expanded") === "true");
        target?.addEventListener("shown.bs.collapse", () => setExpanded(true));
        target?.addEventListener("hidden.bs.collapse", () => setExpanded(false));
    });
}

function setupAuditSectionLinks(root = document) {
    root.querySelectorAll("[data-audit-open-section]").forEach((link) => {
        if (link.dataset.auditOpenSectionReady === "true") {
            return;
        }

        link.dataset.auditOpenSectionReady = "true";
        link.addEventListener("click", (event) => {
            const targetSelector = link.dataset.auditOpenSection || link.getAttribute("href");
            const target = targetSelector ? document.querySelector(targetSelector) : null;
            if (!target) {
                return;
            }

            event.preventDefault();
            if (target.classList.contains("collapse") && window.bootstrap?.Collapse) {
                window.bootstrap.Collapse.getOrCreateInstance(target, { toggle: false }).show();
            }
            target.scrollIntoView({ behavior: "smooth", block: "start" });
            if (target.id) {
                window.history.replaceState(window.history.state, "", `#${target.id}`);
            }
        });
    });
}

function openAuditSectionFromLocation(root = document) {
    const params = new URL(window.location.href).searchParams;
    const sectionId = params.get("open") || window.location.hash.replace(/^#/, "");
    if (!sectionId) {
        return;
    }

    const escapedSectionId = window.CSS?.escape
        ? CSS.escape(sectionId)
        : sectionId.replaceAll('"', '\\"');
    const target = root.querySelector(`#${escapedSectionId}`) || document.getElementById(sectionId);
    if (!target || !target.classList.contains("collapse")) {
        return;
    }

    if (window.bootstrap?.Collapse) {
        window.bootstrap.Collapse.getOrCreateInstance(target, { toggle: false }).show();
    } else {
        target.classList.add("show");
    }
}

setupDashboardDrilldownInteractions();
setupTableRowInteractions();
setupTransactionBatchActions();
setupCollapseToggleLabels();
setupAuditSectionLinks();
openAuditSectionFromLocation();

function setupSortableTables(root = document) {
    const tables = Array.from(root.querySelectorAll("[data-sortable-table]"));

    tables.forEach((table) => {
        if (table.dataset.sortableTableReady === "true") {
            return;
        }

        table.dataset.sortableTableReady = "true";
        const tbody = table.tBodies[0];
        const buttons = Array.from(table.querySelectorAll("[data-sort-column]"));
        if (!tbody || !buttons.length) return;

        function cellValue(row, column, type) {
            const cell = row.cells[column];
            if (!cell) return type === "number" ? 0 : "";
            if (type === "number") {
                const explicit = cell.getAttribute("data-sort-value");
                if (explicit !== null) return Number(explicit) || 0;
                return Number(cell.textContent.replace(/[^0-9.-]/g, "")) || 0;
            }

            return (cell.getAttribute("data-sort-value") || cell.textContent).trim().toLocaleLowerCase();
        }

        function rowSortGroup(row) {
            return Number(row.dataset.sortGroup || 0) || 0;
        }

        function setSortIcon(button, direction) {
            table.querySelectorAll(".sort-icon").forEach((icon) => icon.remove());
            const icon = document.createElement("span");
            icon.className = `sort-icon ${direction}`;
            icon.setAttribute("aria-hidden", "true");
            button.appendChild(icon);
        }

        buttons.forEach((button) => {
            button.addEventListener("click", () => {
                const column = Number(button.dataset.sortColumn || 0);
                const type = button.dataset.sortType || "text";
                const currentDirection = button.dataset.sortDirection || "desc";
                const nextDirection = currentDirection === "asc" ? "desc" : "asc";
                buttons.forEach((item) => delete item.dataset.sortDirection);
                button.dataset.sortDirection = nextDirection;

                const sortableRows = Array.from(tbody.rows).filter((row) => !row.hasAttribute("data-sort-ignore"));
                const ignoredRows = Array.from(tbody.rows).filter((row) => row.hasAttribute("data-sort-ignore"));
                sortableRows.sort((left, right) => {
                    const groupDifference = rowSortGroup(left) - rowSortGroup(right);
                    if (groupDifference !== 0) return groupDifference;

                    const leftValue = cellValue(left, column, type);
                    const rightValue = cellValue(right, column, type);
                    if (leftValue < rightValue) return nextDirection === "asc" ? -1 : 1;
                    if (leftValue > rightValue) return nextDirection === "asc" ? 1 : -1;
                    return 0;
                });

                [...sortableRows, ...ignoredRows].forEach((row) => tbody.appendChild(row));
                setSortIcon(button, nextDirection);
                table.dispatchEvent(new CustomEvent("finance:table-sorted"));
            });
        });
    });
}

setupSortableTables();

function translateTableMessage(message, variables = {}) {
    const translator = window.financeTranslate;
    if (typeof translator === "function") {
        return translator(message, variables);
    }

    return Object.entries(variables).reduce(
        (result, entry) => result.replaceAll(`{${entry[0]}}`, String(entry[1])),
        message
    );
}

function setupPaginatedTables(root = document) {
    const tables = Array.from(root.querySelectorAll("[data-paginated-table]"));

    tables.forEach((table) => {
        if (table.dataset.paginatedTableReady === "true") {
            return;
        }

        const tbody = table.tBodies[0];
        if (!tbody) {
            return;
        }

        table.dataset.paginatedTableReady = "true";
        const pageSize = Math.max(1, Number(table.dataset.pageSize || 25) || 25);
        const state = { page: 1 };
        const paginationLabel = table.dataset.paginationLabel || "Table pages";
        const tableContainer = table.closest(".table-responsive") || table;
        const controls = [
            createPaginationControls("table-pagination-header", paginationLabel),
            createPaginationControls("table-pagination-footer", paginationLabel),
        ];

        tableContainer.insertAdjacentElement("beforebegin", controls[0].container);
        tableContainer.insertAdjacentElement("afterend", controls[1].container);

        function createPaginationControls(className, label) {
            const container = document.createElement("div");
            container.className = `${className} d-flex flex-wrap justify-content-between align-items-center gap-3`;
            const status = document.createElement("div");
            status.className = "text-muted";
            const nav = document.createElement("nav");
            nav.setAttribute("aria-label", label);
            const pagination = document.createElement("ul");
            pagination.className = "pagination mb-0";

            nav.appendChild(pagination);
            container.append(status, nav);
            return { container, status, pagination };
        }

        function tableRows() {
            return Array.from(tbody.rows).filter((row) => !row.hasAttribute("data-sort-ignore"));
        }

        function totalPages(totalRows) {
            return Math.max(1, Math.ceil(totalRows / pageSize));
        }

        function setPage(pageNumber) {
            state.page = Math.min(Math.max(1, pageNumber), totalPages(tableRows().length));
            render();
        }

        function addPageButton(pagination, label, pageNumber, options = {}) {
            const item = document.createElement("li");
            item.className = `page-item${options.active ? " active" : ""}${options.disabled ? " disabled" : ""}`;
            const button = document.createElement("button");
            button.className = "page-link";
            button.type = "button";
            button.textContent = label;
            button.disabled = Boolean(options.disabled);
            if (options.active) {
                button.setAttribute("aria-current", "page");
            }
            button.addEventListener("click", () => setPage(pageNumber));
            item.appendChild(button);
            pagination.appendChild(item);
        }

        function addEllipsis(pagination) {
            const item = document.createElement("li");
            item.className = "page-item disabled";
            const marker = document.createElement("span");
            marker.className = "page-link";
            marker.textContent = "...";
            item.appendChild(marker);
            pagination.appendChild(item);
        }

        function visiblePageNumbers(pageCount) {
            const pages = new Set([1, pageCount]);
            for (let page = state.page - 2; page <= state.page + 2; page += 1) {
                if (page >= 1 && page <= pageCount) {
                    pages.add(page);
                }
            }
            return Array.from(pages).sort((left, right) => left - right);
        }

        function renderControls(control, pageCount, startIndex, endIndex, totalRows) {
            control.status.textContent = translateTableMessage(
                "Showing {start}-{end} of {total} rows",
                {
                    start: startIndex + 1,
                    end: endIndex,
                    total: totalRows,
                }
            );
            control.pagination.replaceChildren();
            addPageButton(control.pagination, translateTableMessage("Previous"), state.page - 1, {
                disabled: state.page <= 1,
            });

            let previousPage = 0;
            visiblePageNumbers(pageCount).forEach((pageNumber) => {
                if (previousPage && pageNumber - previousPage > 1) {
                    addEllipsis(control.pagination);
                }
                addPageButton(control.pagination, String(pageNumber), pageNumber, {
                    active: pageNumber === state.page,
                });
                previousPage = pageNumber;
            });

            addPageButton(control.pagination, translateTableMessage("Next"), state.page + 1, {
                disabled: state.page >= pageCount,
            });
        }

        function render() {
            const rows = tableRows();
            const totalRows = rows.length;
            const pageCount = totalPages(totalRows);
            state.page = Math.min(state.page, pageCount);
            const startIndex = totalRows ? (state.page - 1) * pageSize : 0;
            const endIndex = Math.min(startIndex + pageSize, totalRows);

            rows.forEach((row, index) => {
                row.hidden = totalRows > pageSize && (index < startIndex || index >= endIndex);
            });

            controls.forEach((control) => {
                control.container.hidden = totalRows <= pageSize;
            });
            if (totalRows <= pageSize) {
                return;
            }

            controls.forEach((control) => renderControls(control, pageCount, startIndex, endIndex, totalRows));
        }

        table.addEventListener("finance:table-sorted", () => {
            state.page = 1;
            render();
        });
        render();
    });
}

setupPaginatedTables();

window.setupCollapseToggleLabels = setupCollapseToggleLabels;
window.setupAuditSectionLinks = setupAuditSectionLinks;
window.openAuditSectionFromLocation = openAuditSectionFromLocation;
window.setupTableRowInteractions = setupTableRowInteractions;
window.setupTransactionBatchActions = setupTransactionBatchActions;
window.setupSortableTables = setupSortableTables;
window.setupPaginatedTables = setupPaginatedTables;
