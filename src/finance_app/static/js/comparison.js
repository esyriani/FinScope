function setupComparisonChangeFilters(root = document) {
    const filterGroups = [
        ...(root.matches?.("[data-comparison-change-filters]") ? [root] : []),
        ...Array.from(root.querySelectorAll("[data-comparison-change-filters]")),
    ];

    filterGroups.forEach((group) => {
        if (group.dataset.comparisonChangeFiltersReady === "true") {
            return;
        }

        const section = group.closest("section");
        const rows = Array.from(section?.querySelectorAll("[data-comparison-change-row]") || []);
        const buttons = Array.from(group.querySelectorAll("[data-comparison-change-filter]"));

        if (!rows.length || !buttons.length) return;

        function rowMatches(row, filter) {
            if (filter === "all") return true;
            if (filter === "increases") return row.dataset.changeDirection === "up";
            if (filter === "decreases") return row.dataset.changeDirection === "down";
            return row.dataset.changeState === filter;
        }

        function applyFilter(filter) {
            rows.forEach((row) => {
                row.hidden = !rowMatches(row, filter);
            });

            buttons.forEach((button) => {
                const active = button.dataset.comparisonChangeFilter === filter;
                button.classList.toggle("btn-primary", active);
                button.classList.toggle("btn-outline-secondary", !active);
                button.setAttribute("aria-pressed", active ? "true" : "false");
            });
        }

        group.dataset.comparisonChangeFiltersReady = "true";
        buttons.forEach((button) => {
            button.addEventListener("click", () => applyFilter(button.dataset.comparisonChangeFilter || "all"));
        });

        applyFilter("all");
    });
}

const comparisonTabViews = {
    "comparison-period-tab": "period",
    "comparison-year-tab": "year",
};

function comparisonViewForTab(tab) {
    return comparisonTabViews[tab?.id] || "";
}

function updateComparisonViewQuery(tab) {
    const view = comparisonViewForTab(tab);
    if (!view || !window.history?.replaceState) return;

    const url = new URL(window.location.href);
    if (url.searchParams.get("comparison_view") === view) return;

    url.searchParams.set("comparison_view", view);
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

function setupComparisonTabs(root = document) {
    const tabs = [
        ...(root.matches?.("[data-bs-toggle='tab']") ? [root] : []),
        ...Array.from(root.querySelectorAll("[data-bs-toggle='tab']")),
    ].filter(comparisonViewForTab);

    tabs.forEach((tab) => {
        if (tab.dataset.comparisonTabReady === "true") {
            return;
        }

        tab.dataset.comparisonTabReady = "true";
        tab.addEventListener("shown.bs.tab", () => {
            updateComparisonViewQuery(tab);
            window.dispatchEvent(new CustomEvent("finance:layoutchange"));
        });
    });
}

function comparisonTableCellValue(row, column, type) {
    const cell = row.cells[column];
    if (!cell) return type === "number" ? 0 : "";
    if (type === "number") {
        const explicit = cell.getAttribute("data-sort-value");
        if (explicit !== null) return Number(explicit) || 0;
        return Number(cell.textContent.replace(/[^0-9.-]/g, "")) || 0;
    }
    return cell.textContent.trim().toLocaleLowerCase();
}

function comparisonAriaSortDirection(direction) {
    if (direction === "asc") return "ascending";
    if (direction === "desc") return "descending";
    return "";
}

function comparisonSortControlDirection(button) {
    if (button.dataset.sortDirection === "asc" || button.dataset.sortDirection === "desc") {
        return button.dataset.sortDirection;
    }

    const icon = button.querySelector(".sort-icon.asc, .sort-icon.desc");
    if (icon?.classList.contains("asc")) return "asc";
    if (icon?.classList.contains("desc")) return "desc";
    return "";
}

function setComparisonSortState(table, button, direction) {
    table.querySelectorAll(".sort-icon").forEach((icon) => icon.remove());
    table.querySelectorAll("th[aria-sort]").forEach((header) => header.removeAttribute("aria-sort"));

    const icon = document.createElement("span");
    icon.className = `sort-icon ${direction}`;
    icon.setAttribute("aria-hidden", "true");
    button.appendChild(icon);

    const ariaSort = comparisonAriaSortDirection(direction);
    if (ariaSort) {
        button.closest("th")?.setAttribute("aria-sort", ariaSort);
    }
}

function setupComparisonTableSorting(root = document) {
    const tables = [
        ...(root.matches?.("[data-comparison-sortable]") ? [root] : []),
        ...Array.from(root.querySelectorAll("[data-comparison-sortable]")),
    ];

    tables.forEach((table) => {
        if (table.dataset.comparisonSortableReady === "true") {
            return;
        }

        const tbody = table.tBodies[0];
        const buttons = Array.from(table.querySelectorAll("[data-sort-column]"));
        if (!tbody || !buttons.length) return;

        table.dataset.comparisonSortableReady = "true";
        const initialSortedButton = buttons.find((button) => comparisonSortControlDirection(button));
        if (initialSortedButton) {
            const initialDirection = comparisonSortControlDirection(initialSortedButton);
            initialSortedButton.dataset.sortDirection = initialDirection;
            setComparisonSortState(table, initialSortedButton, initialDirection);
        }

        buttons.forEach((button) => {
            button.addEventListener("click", () => {
                const column = Number(button.dataset.sortColumn || 0);
                const type = button.dataset.sortType || "text";
                const currentDirection = button.dataset.sortDirection || "desc";
                const nextDirection = currentDirection === "asc" ? "desc" : "asc";
                buttons.forEach((item) => delete item.dataset.sortDirection);
                button.dataset.sortDirection = nextDirection;

                const rows = Array.from(tbody.rows);
                rows.sort((left, right) => {
                    const leftValue = comparisonTableCellValue(left, column, type);
                    const rightValue = comparisonTableCellValue(right, column, type);
                    if (leftValue < rightValue) return nextDirection === "asc" ? -1 : 1;
                    if (leftValue > rightValue) return nextDirection === "asc" ? 1 : -1;
                    return 0;
                });

                rows.forEach((row) => tbody.appendChild(row));
                setComparisonSortState(table, button, nextDirection);
            });
        });
    });
}

window.financeApp?.registerInitializer("comparison.change-filters", setupComparisonChangeFilters);
window.financeApp?.registerInitializer("comparison.tabs", setupComparisonTabs);
window.financeApp?.registerInitializer("comparison.table-sorting", setupComparisonTableSorting);

setupComparisonChangeFilters();
setupComparisonTabs();
setupComparisonTableSorting();
