function reportsRoot(root) {
    return root && typeof root.querySelector === "function" ? root : document;
}

function reportsScopedElement(root, selector) {
    return root.matches?.(selector) ? root : root.querySelector(selector);
}

function setupReportsCustomRange(root = document) {
    const periodSelect = reportsScopedElement(root, "#reports-period");
    if (!periodSelect) return;

    const fields = root.querySelectorAll("[data-reports-custom-range]");
    const dateInputs = root.querySelectorAll("[data-reports-custom-range] [data-flatpickr-date]");
    const updateVisibility = () => {
        const isCustom = periodSelect.value === "custom";
        fields.forEach((field) => field.classList.toggle("d-none", !isCustom));
        dateInputs.forEach((input) => {
            input.required = isCustom;
        });
    };

    if (periodSelect.dataset.reportsPeriodReady !== "true") {
        periodSelect.dataset.reportsPeriodReady = "true";
        periodSelect.addEventListener("change", updateVisibility);
    }
    updateVisibility();
}

function normalizeReportsSearchText(value) {
    return String(value || "")
        .replace(/\s+/g, " ")
        .trim()
        .toLocaleLowerCase();
}

function reportsUrlWithTaxonomyState(href, state) {
    let url;
    try {
        url = new URL(href, window.location.origin);
    } catch (_error) {
        return href;
    }

    if (state.filter && state.filter !== "all") {
        url.searchParams.set("taxonomy_filter", state.filter);
    } else {
        url.searchParams.delete("taxonomy_filter");
    }

    if (state.search) {
        url.searchParams.set("taxonomy_search", state.search);
    } else {
        url.searchParams.delete("taxonomy_search");
    }

    if (url.origin === window.location.origin) {
        return `${url.pathname}${url.search}${url.hash}`;
    }
    return url.href;
}

function currentTaxonomyExplorerState(root = document) {
    const explorer = reportsScopedElement(root, "[data-taxonomy-explorer]");
    const searchInput = reportsScopedElement(root, "[data-taxonomy-explorer-search]");
    const activeFilter = explorer?.querySelector("[data-taxonomy-filter][aria-pressed='true']");
    return {
        filter: activeFilter?.dataset.taxonomyFilter || "all",
        search: searchInput?.value || "",
    };
}

function setupTaxonomyOpenControls(root = document) {
    root.querySelectorAll("[data-taxonomy-open-control]").forEach((control) => {
        if (control.dataset.taxonomyOpenReady === "true") {
            return;
        }

        const input = control.querySelector("[data-taxonomy-open-input]");
        const menu = control.querySelector("[data-taxonomy-open-menu]");
        const optionsScript = control.querySelector("[data-taxonomy-open-options]");
        if (!input || !menu || !optionsScript) {
            return;
        }

        let targets = [];
        try {
            const parsed = JSON.parse(optionsScript.textContent || "[]");
            targets = Array.isArray(parsed) ? parsed : [];
        } catch (_error) {
            targets = [];
        }
        if (!targets.length) {
            return;
        }

        control.dataset.taxonomyOpenReady = "true";
        let activeIndex = -1;
        let suggestions = [];
        let debounceId = 0;
        const suggestionsLimit = Math.max(1, Number(control.dataset.suggestionsLimit || 8) || 8);

        function setExpanded(expanded) {
            input.setAttribute("aria-expanded", expanded ? "true" : "false");
            menu.hidden = !expanded;
        }

        function clearMenu() {
            suggestions = [];
            activeIndex = -1;
            menu.replaceChildren();
            setExpanded(false);
        }

        function renderStatus(message) {
            const status = document.createElement("div");
            status.className = "merchant-autocomplete-status";
            status.setAttribute("role", "option");
            status.setAttribute("aria-disabled", "true");
            status.textContent = message;
            menu.replaceChildren(status);
            setExpanded(true);
        }

        function updateActiveOption() {
            const options = Array.from(menu.querySelectorAll("[data-taxonomy-open-option]"));
            options.forEach((option, index) => {
                const active = index === activeIndex;
                option.classList.toggle("active", active);
                option.setAttribute("aria-selected", active ? "true" : "false");
            });
        }

        function targetUrl(target) {
            return reportsUrlWithTaxonomyState(target.url || "", currentTaxonomyExplorerState(root));
        }

        function openTarget(target) {
            const url = targetUrl(target);
            if (url) {
                window.location.href = url;
            }
        }

        function renderSuggestions(items) {
            suggestions = items;
            activeIndex = -1;
            menu.replaceChildren();
            if (!items.length) {
                renderStatus(
                    window.financeTranslate?.("No categories or tags found.") || "No categories or tags found."
                );
                return;
            }

            items.forEach((target, index) => {
                const option = document.createElement("button");
                option.type = "button";
                option.className = "merchant-autocomplete-option reports-taxonomy-open-option";
                option.id = `${menu.id || input.id}-option-${index}`;
                option.setAttribute("role", "option");
                option.setAttribute("aria-selected", "false");
                option.dataset.taxonomyOpenOption = "true";

                const label = document.createElement("span");
                label.className = "reports-taxonomy-open-label";
                label.textContent = target.label || "";
                const type = document.createElement("small");
                type.className = "reports-taxonomy-open-type";
                type.textContent = target.type_label || "";
                option.append(label, type);

                option.addEventListener("mousedown", (event) => event.preventDefault());
                option.addEventListener("click", () => openTarget(target));
                menu.appendChild(option);
            });
            setExpanded(true);
        }

        function matchingTargets(query) {
            const normalizedQuery = normalizeReportsSearchText(query);
            return targets
                .filter((target) =>
                    normalizeReportsSearchText(target.search_text || target.display_label).includes(normalizedQuery)
                )
                .slice(0, suggestionsLimit);
        }

        function exactTarget() {
            const normalizedValue = normalizeReportsSearchText(input.value);
            if (!normalizedValue) {
                return null;
            }
            return (
                targets.find((target) => normalizeReportsSearchText(target.display_label) === normalizedValue) || null
            );
        }

        function scheduleSearch() {
            const query = input.value.trim();
            window.clearTimeout(debounceId);
            if (query.length < 2) {
                clearMenu();
                return;
            }
            debounceId = window.setTimeout(() => renderSuggestions(matchingTargets(query)), 160);
        }

        input.addEventListener("input", scheduleSearch);
        input.addEventListener("focus", scheduleSearch);
        input.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                clearMenu();
                return;
            }
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                if (!suggestions.length || menu.hidden) {
                    return;
                }
                event.preventDefault();
                const step = event.key === "ArrowDown" ? 1 : -1;
                activeIndex = (activeIndex + step + suggestions.length) % suggestions.length;
                updateActiveOption();
                return;
            }
            if (event.key === "Enter") {
                if (activeIndex >= 0 && suggestions[activeIndex]) {
                    event.preventDefault();
                    openTarget(suggestions[activeIndex]);
                    return;
                }
                const target = exactTarget();
                if (target) {
                    event.preventDefault();
                    openTarget(target);
                }
            }
        });

        document.addEventListener("click", (event) => {
            if (!control.contains(event.target)) {
                clearMenu();
            }
        });
    });
}

function setupTaxonomyTargetSwitchers(root = document) {
    root.querySelectorAll("[data-taxonomy-target-switcher]").forEach((switcher) => {
        if (switcher.dataset.taxonomySwitcherReady === "true") {
            return;
        }

        const search = switcher.querySelector("[data-taxonomy-target-search]");
        const options = Array.from(switcher.querySelectorAll("[data-taxonomy-target-option]"));
        if (!search || options.length === 0) {
            return;
        }

        function applySearch() {
            const query = normalizeReportsSearchText(search.value);
            options.forEach((option) => {
                const matches = !query || normalizeReportsSearchText(option.dataset.searchText).includes(query);
                option.hidden = !matches;
            });
        }

        switcher.dataset.taxonomySwitcherReady = "true";
        search.addEventListener("input", applySearch);
        switcher.addEventListener("shown.bs.dropdown", () => {
            search.focus();
            search.select();
        });
        applySearch();
    });
}

function setupTaxonomyExplorer(root = document) {
    root.querySelectorAll("[data-taxonomy-explorer]").forEach((explorer) => {
        if (explorer.dataset.taxonomyExplorerReady === "true") {
            return;
        }

        const table = explorer.querySelector("table");
        const body = explorer.querySelector("[data-taxonomy-explorer-body]");
        const filterButtons = Array.from(explorer.querySelectorAll("[data-taxonomy-filter]"));
        const searchInput =
            reportsScopedElement(root, "[data-taxonomy-explorer-search]") ||
            document.querySelector("[data-taxonomy-explorer-search]");
        if (!table || !body || filterButtons.length === 0) {
            return;
        }

        const rows = Array.from(body.querySelectorAll("[data-taxonomy-explorer-row]"));
        const activeButton =
            filterButtons.find((button) => button.getAttribute("aria-pressed") === "true") || filterButtons[0];
        const state = { filter: activeButton?.dataset.taxonomyFilter || "all" };

        function explorerState() {
            return {
                filter: state.filter,
                search: searchInput?.value || "",
            };
        }

        function syncReportLinks() {
            const currentState = explorerState();
            rows.forEach((row) => {
                const baseRowHref = row.dataset.baseRowHref || row.dataset.rowHref || "";
                const rowHref = reportsUrlWithTaxonomyState(baseRowHref, currentState);
                row.dataset.rowHref = rowHref;
                row.querySelectorAll("[data-taxonomy-report-link]").forEach((link) => {
                    const baseUrl = link.dataset.baseUrl || link.getAttribute("href") || "";
                    link.setAttribute("href", reportsUrlWithTaxonomyState(baseUrl, currentState));
                });
            });
        }

        function syncLocationState() {
            if (typeof window.history?.replaceState !== "function") {
                return;
            }
            const nextUrl = reportsUrlWithTaxonomyState(window.location.href, explorerState());
            window.history.replaceState(window.history.state, "", nextUrl);
        }

        function syncExplorerState({ replaceLocation = true } = {}) {
            syncReportLinks();
            if (replaceLocation) {
                syncLocationState();
            }
        }

        function rowMatchesFilter(row) {
            if (state.filter === "categories") return row.dataset.kind === "category";
            if (state.filter === "tags") return row.dataset.kind === "tag";
            if (state.filter === "analytics-categories") return row.dataset.analyticsCategory === "true";
            if (state.filter === "has-income") return row.dataset.hasIncome === "true";
            if (state.filter === "has-spending") return row.dataset.hasSpending === "true";
            return true;
        }

        function rowMatchesSearch(row) {
            const query = normalizeReportsSearchText(searchInput?.value || "");
            return !query || normalizeReportsSearchText(row.dataset.searchText).includes(query);
        }

        function applyFilter() {
            rows.forEach((row) => {
                if (rowMatchesFilter(row) && rowMatchesSearch(row)) {
                    delete row.dataset.tableFilteredOut;
                } else {
                    row.dataset.tableFilteredOut = "true";
                }
            });
            table.dispatchEvent(new CustomEvent("finance:table-filtered"));
        }

        function setActiveFilter(button) {
            state.filter = button.dataset.taxonomyFilter || "all";
            filterButtons.forEach((filterButton) => {
                const isActive = filterButton === button;
                filterButton.classList.toggle("btn-primary", isActive);
                filterButton.classList.toggle("btn-outline-secondary", !isActive);
                filterButton.setAttribute("aria-pressed", isActive ? "true" : "false");
            });
            applyFilter();
            syncExplorerState();
        }

        explorer.dataset.taxonomyExplorerReady = "true";
        filterButtons.forEach((button) => {
            button.addEventListener("click", () => setActiveFilter(button));
        });
        searchInput?.addEventListener("input", () => {
            applyFilter();
            syncExplorerState();
        });
        applyFilter();
        syncExplorerState({ replaceLocation: false });
    });
}

function setupReportsPage(root = document) {
    root = reportsRoot(root);
    if (!reportsScopedElement(root, "[data-reports-page]")) return;

    if (typeof setupFlatpickrInputs === "function") {
        setupFlatpickrInputs(root);
    }
    setupReportsCustomRange(root);
    setupTaxonomyOpenControls(root);
    setupTaxonomyTargetSwitchers(root);
    setupTaxonomyExplorer(root);
}

window.financeApp?.registerInitializer("reports.page", setupReportsPage);

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setupReportsPage());
} else {
    setupReportsPage();
}
