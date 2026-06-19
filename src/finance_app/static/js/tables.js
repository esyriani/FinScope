function setupTableRowInteractions(root = document) {
    const interactiveSelector = "a, button, input, select, textarea, form, [data-row-action]";

    function rowHref(row) {
        return row.getAttribute("data-row-href") || "";
    }

    function rowEditTarget(row) {
        const targetSelector = row.getAttribute("data-row-edit-target");
        return targetSelector ? document.querySelector(targetSelector) : null;
    }

    function selectRow(row) {
        const table = row.closest("table");
        table?.querySelectorAll("tbody tr.table-row-selected").forEach((selectedRow) => {
            selectedRow.classList.remove("table-row-selected");
        });
        row.classList.add("table-row-selected");
    }

    function openRowEditTarget(row) {
        const modalElement = rowEditTarget(row);
        if (!modalElement || !window.bootstrap?.Modal) {
            return false;
        }

        if (typeof window.financeApp?.showModalAfterExpandedExportCloses === "function") {
            window.financeApp.showModalAfterExpandedExportCloses(modalElement);
        } else {
            window.bootstrap.Modal.getOrCreateInstance(modalElement).show();
        }
        return true;
    }

    function navigateRow(row, href) {
        if (href) {
            window.showBusyOverlayForElement?.(row);
            window.location.href = href;
            return true;
        }

        return false;
    }

    function activateRowFromClick(row) {
        const href = rowHref(row);
        if (href) {
            if (row.dataset.rowDrilldown === "dblclick") {
                if (typeof selectDashboardDrilldownItem === "function") {
                    selectDashboardDrilldownItem(row);
                } else {
                    selectRow(row);
                }
                return;
            }

            navigateRow(row, href);
            return;
        }

        selectRow(row);
    }

    function activateRowFromKeyboard(row) {
        const href = rowHref(row);
        if (href) {
            navigateRow(row, href);
            return;
        }

        if (openRowEditTarget(row)) {
            return;
        }

        selectRow(row);
    }

    function prepareInteractiveRow(row) {
        if (!rowHref(row) && !row.getAttribute("data-row-edit-target")) {
            return;
        }

        if (!row.hasAttribute("tabindex")) {
            row.tabIndex = 0;
        }
        if (!row.hasAttribute("role")) {
            row.setAttribute("role", "button");
        }
        if (!row.hasAttribute("aria-label") && !row.hasAttribute("aria-labelledby")) {
            row.setAttribute("aria-label", row.textContent.replace(/\s+/g, " ").trim());
        }
    }

    root.querySelectorAll("table:not([data-no-row-select]) tbody tr").forEach((row) => {
        if (row.dataset.tableRowInteractionReady === "true") {
            return;
        }

        row.dataset.tableRowInteractionReady = "true";
        prepareInteractiveRow(row);

        row.addEventListener("click", (event) => {
            if (event.target.closest(interactiveSelector)) {
                return;
            }

            activateRowFromClick(row);
        });

        row.addEventListener("dblclick", (event) => {
            if (event.target.closest(interactiveSelector)) {
                return;
            }

            const href = rowHref(row);
            if (href && row.dataset.rowDrilldown === "dblclick") {
                navigateRow(row, href);
                return;
            }

            openRowEditTarget(row);
        });

        row.addEventListener("keydown", (event) => {
            if (event.target.closest(interactiveSelector)) {
                return;
            }

            if (event.key !== "Enter" && event.key !== " ") {
                return;
            }

            event.preventDefault();
            activateRowFromKeyboard(row);
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
                countLabel.textContent = financeTranslate("{count} selected", { count: selectedCount });
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

const collapsePanelHeaderInteractiveSelector = [
    "a",
    "button",
    "input",
    "select",
    "textarea",
    "label",
    "summary",
    "[role='button']",
    "[data-bs-toggle]",
].join(", ");

function collapseTargetForToggle(toggle) {
    const targetSelector =
        toggle.dataset.collapsePanelTarget ||
        toggle.dataset.filterPanelTarget ||
        toggle.getAttribute("data-bs-target") ||
        "";
    return targetSelector ? document.querySelector(targetSelector) : null;
}

const collapsePanelHeadingSelector = "[data-filter-panel-heading-toggle], [data-collapse-panel-heading-toggle]";
const collapsePanelHeaderSelector = "[data-filter-panel-header-toggle], [data-collapse-panel-header-toggle]";

function filterPanelHeadingToggles(target) {
    if (!target?.id) return [];
    return Array.from(document.querySelectorAll(collapsePanelHeadingSelector)).filter(
        (heading) => heading.getAttribute("aria-controls") === target.id
    );
}

function setFilterPanelHeadingExpanded(target, expanded) {
    filterPanelHeadingToggles(target).forEach((heading) => {
        heading.setAttribute("aria-expanded", expanded ? "true" : "false");
    });
}

function toggleFilterPanelTarget(target) {
    if (!target) return;

    if (window.bootstrap?.Collapse) {
        window.bootstrap.Collapse.getOrCreateInstance(target, { toggle: false }).toggle();
        return;
    }

    target.classList.toggle("show");
    setFilterPanelHeadingExpanded(target, target.classList.contains("show"));
}

function setupCollapsePanelStateSync(target) {
    if (!target || target.dataset.collapsePanelStateReady === "true") {
        return;
    }

    target.dataset.collapsePanelStateReady = "true";
    setFilterPanelHeadingExpanded(target, target.classList.contains("show"));
    target.addEventListener("shown.bs.collapse", () => setFilterPanelHeadingExpanded(target, true));
    target.addEventListener("hidden.bs.collapse", () => setFilterPanelHeadingExpanded(target, false));
}

function setupFilterPanelHeaderToggles(root = document) {
    root.querySelectorAll(collapsePanelHeaderSelector).forEach((header) => {
        if (header.dataset.collapsePanelHeaderReady === "true") {
            return;
        }

        const target = collapseTargetForToggle(header);
        if (!target) return;
        setupCollapsePanelStateSync(target);

        header.dataset.collapsePanelHeaderReady = "true";
        header.addEventListener("click", (event) => {
            const headingToggle = event.target.closest(collapsePanelHeadingSelector);
            const interactiveElement = event.target.closest(collapsePanelHeaderInteractiveSelector);
            if (interactiveElement && !headingToggle?.contains(interactiveElement)) {
                return;
            }

            toggleFilterPanelTarget(target);
        });
    });

    root.querySelectorAll(collapsePanelHeadingSelector).forEach((heading) => {
        if (heading.dataset.collapsePanelHeadingReady === "true") {
            return;
        }

        const target = collapseTargetForToggle(heading);
        if (!target) return;
        setupCollapsePanelStateSync(target);

        heading.dataset.collapsePanelHeadingReady = "true";
        heading.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" && event.key !== " ") {
                return;
            }

            event.preventDefault();
            toggleFilterPanelTarget(target);
        });
    });
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
            setFilterPanelHeadingExpanded(target, expanded);
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

    const escapedSectionId = window.CSS?.escape ? CSS.escape(sectionId) : sectionId.replaceAll('"', '\\"');
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

setupTableRowInteractions();
setupTransactionBatchActions();
setupFilterPanelHeaderToggles();
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

function normalizeTableSearchText(value) {
    return String(value || "")
        .replace(/\s+/g, " ")
        .trim()
        .toLocaleLowerCase();
}

function setupTableSearch(root = document) {
    root.querySelectorAll("[data-table-search]").forEach((input) => {
        if (input.dataset.tableSearchReady === "true") {
            return;
        }

        const targetSelector = input.dataset.tableSearchTarget || "";
        const table = targetSelector
            ? root.querySelector(targetSelector) || document.querySelector(targetSelector)
            : input.closest(".card")?.querySelector("table");
        const tbody = table?.tBodies[0];
        if (!table || !tbody) {
            return;
        }

        input.dataset.tableSearchReady = "true";

        function applySearch() {
            const query = normalizeTableSearchText(input.value);
            Array.from(tbody.rows).forEach((row) => {
                if (row.hasAttribute("data-sort-ignore")) {
                    return;
                }

                const matches = !query || normalizeTableSearchText(row.textContent).includes(query);
                if (matches) {
                    delete row.dataset.tableFilteredOut;
                } else {
                    row.dataset.tableFilteredOut = "true";
                }
                row.hidden = !matches;
            });
            table.dispatchEvent(new CustomEvent("finance:table-filtered"));
        }

        input.addEventListener("input", applySearch);
        applySearch();
    });
}

setupTableSearch();

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

        function allTableRows() {
            return Array.from(tbody.rows).filter((row) => !row.hasAttribute("data-sort-ignore"));
        }

        function tableRows() {
            return allTableRows().filter((row) => row.dataset.tableFilteredOut !== "true");
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
            control.status.textContent = translateTableMessage("Showing {start}-{end} of {total} rows", {
                start: startIndex + 1,
                end: endIndex,
                total: totalRows,
            });
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
            allTableRows().forEach((row) => {
                row.hidden = row.dataset.tableFilteredOut === "true";
            });
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
        table.addEventListener("finance:table-filtered", () => {
            state.page = 1;
            render();
        });
        render();
    });
}

setupPaginatedTables();

window.financeApp?.registerInitializer("tables.row-interactions", setupTableRowInteractions);
window.financeApp?.registerInitializer("tables.transaction-batch-actions", setupTransactionBatchActions);
window.financeApp?.registerInitializer("tables.filter-panel-header-toggles", setupFilterPanelHeaderToggles);
window.financeApp?.registerInitializer("tables.collapse-toggle-labels", setupCollapseToggleLabels);
window.financeApp?.registerInitializer("tables.audit-section-links", setupAuditSectionLinks);
window.financeApp?.registerInitializer("tables.open-audit-section", openAuditSectionFromLocation);
window.financeApp?.registerInitializer("tables.sortable", setupSortableTables);
window.financeApp?.registerInitializer("tables.search", setupTableSearch);
window.financeApp?.registerInitializer("tables.paginated", setupPaginatedTables);
