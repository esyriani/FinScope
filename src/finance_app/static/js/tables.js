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

setupDashboardDrilldownInteractions();
setupTableRowInteractions();

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
                    const leftValue = cellValue(left, column, type);
                    const rightValue = cellValue(right, column, type);
                    if (leftValue < rightValue) return nextDirection === "asc" ? -1 : 1;
                    if (leftValue > rightValue) return nextDirection === "asc" ? 1 : -1;
                    return 0;
                });

                [...sortableRows, ...ignoredRows].forEach((row) => tbody.appendChild(row));
                setSortIcon(button, nextDirection);
            });
        });
    });
}

setupSortableTables();
