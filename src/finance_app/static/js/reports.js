function reportsRoot(root) {
    return root && typeof root.querySelector === "function" ? root : document;
}

function reportsScopedElement(root, selector) {
    return root.matches?.(selector) ? root : root.querySelector(selector);
}

function reportsScopedElements(root, selector) {
    const scope = reportsRoot(root);
    const elements = Array.from(scope.querySelectorAll(selector));
    if (scope.matches?.(selector)) {
        elements.unshift(scope);
    }
    return elements;
}

function reportsTranslate(message, variables) {
    return window.financeTranslate ? window.financeTranslate(message, variables) : message;
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

function setupReportsScopeRefiners(root = document) {
    root.querySelectorAll("[data-reports-scope-controls]").forEach((control) => {
        if (control.dataset.reportsScopeReady === "true") {
            return;
        }

        const form = control.closest("form");
        const refiners = Array.from(form?.querySelectorAll("[data-reports-scope-refiner]") || []);
        const radios = Array.from(control.querySelectorAll("input[name='quick_view']"));
        if (!form || refiners.length === 0 || radios.length === 0) {
            return;
        }

        const categorizedScope = control.dataset.categorizedScope || "categorized";

        function syncRefiners() {
            const activeScope = radios.find((radio) => radio.checked)?.value || "";
            const showRefiners = activeScope === categorizedScope;
            refiners.forEach((refiner) => {
                refiner.classList.toggle("d-none", !showRefiners);
                refiner.querySelectorAll("input, button").forEach((input) => {
                    input.disabled = !showRefiners;
                });
                refiner.querySelectorAll("[data-tag-multiselect]").forEach((multiselect) => {
                    multiselect.dataset.disabled = showRefiners ? "false" : "true";
                    if (!showRefiners) {
                        const menu = multiselect.querySelector("[data-tag-multiselect-menu]");
                        const toggle = multiselect.querySelector("[data-tag-multiselect-toggle]");
                        if (menu) {
                            menu.style.display = "none";
                        }
                        toggle?.setAttribute("aria-expanded", "false");
                    }
                });
            });
            window.financeApp?.runInitializers(form);
        }

        control.dataset.reportsScopeReady = "true";
        radios.forEach((radio) => {
            radio.addEventListener("change", syncRefiners);
        });
        syncRefiners();
    });
}

function normalizeReportsSearchText(value) {
    return String(value || "")
        .replace(/\s+/g, " ")
        .trim()
        .toLocaleLowerCase();
}

function reportsUrlWithExplorerState(href, state, filterParam, searchParam) {
    let url;
    try {
        url = new URL(href, window.location.origin);
    } catch (_error) {
        return href;
    }

    if (state.filter && state.filter !== "all") {
        url.searchParams.set(filterParam, state.filter);
    } else {
        url.searchParams.delete(filterParam);
    }

    if (state.search) {
        url.searchParams.set(searchParam, state.search);
    } else {
        url.searchParams.delete(searchParam);
    }

    if (url.origin === window.location.origin) {
        return `${url.pathname}${url.search}${url.hash}`;
    }
    return url.href;
}

function reportsUrlWithTaxonomyState(href, state) {
    return reportsUrlWithExplorerState(href, state, "taxonomy_filter", "taxonomy_search");
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

function currentReportExplorerState(root = document, source = null) {
    const explorer = reportsScopedElement(root, "[data-report-explorer]");
    const searchSelector =
        source?.dataset.reportOpenSearchSelector ||
        explorer?.dataset.reportSearchSelector ||
        "[data-report-explorer-search]";
    const searchInput =
        (searchSelector ? reportsScopedElement(root, searchSelector) : null) ||
        (searchSelector ? document.querySelector(searchSelector) : null);
    const activeFilter = explorer?.querySelector("[data-report-filter][aria-pressed='true']");
    return {
        filter: activeFilter?.dataset.reportFilter || "all",
        search: searchInput?.value || "",
    };
}

function setupReportOpenControls(root = document) {
    root.querySelectorAll("[data-report-open-control]").forEach((control) => {
        if (control.dataset.reportOpenReady === "true") {
            return;
        }

        const input = control.querySelector("[data-report-open-input]");
        const menu = control.querySelector("[data-report-open-menu]");
        const optionsScript = control.querySelector("[data-report-open-options]");
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

        control.dataset.reportOpenReady = "true";
        let activeIndex = -1;
        let suggestions = [];
        let debounceId = 0;
        const suggestionsLimit = Math.max(1, Number(control.dataset.suggestionsLimit || 8) || 8);
        const filterParam = control.dataset.reportOpenFilterParam || "entity_filter";
        const searchParam = control.dataset.reportOpenSearchParam || "entity_search";
        const noResultsText =
            window.financeTranslate?.(control.dataset.noResultsText || "No report targets found.") ||
            control.dataset.noResultsText ||
            "No report targets found.";

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
            const options = Array.from(menu.querySelectorAll("[data-report-open-option]"));
            options.forEach((option, index) => {
                const active = index === activeIndex;
                option.classList.toggle("active", active);
                option.setAttribute("aria-selected", active ? "true" : "false");
            });
        }

        function targetUrl(target) {
            return reportsUrlWithExplorerState(
                target.url || "",
                currentReportExplorerState(root, control),
                filterParam,
                searchParam
            );
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
                renderStatus(noResultsText);
                return;
            }

            items.forEach((target, index) => {
                const option = document.createElement("button");
                option.type = "button";
                option.className = "merchant-autocomplete-option reports-taxonomy-open-option";
                option.id = `${menu.id || input.id}-option-${index}`;
                option.setAttribute("role", "option");
                option.setAttribute("aria-selected", "false");
                option.dataset.reportOpenOption = "true";

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

function setupReportTargetSwitchers(root = document) {
    root.querySelectorAll("[data-report-target-switcher]").forEach((switcher) => {
        if (switcher.dataset.reportSwitcherReady === "true") {
            return;
        }

        const search = switcher.querySelector("[data-report-target-search]");
        const options = Array.from(switcher.querySelectorAll("[data-report-target-option]"));
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

        switcher.dataset.reportSwitcherReady = "true";
        search.addEventListener("input", applySearch);
        switcher.addEventListener("shown.bs.dropdown", () => {
            search.focus();
            search.select();
        });
        applySearch();
    });
}

function setupReportExplorers(root = document) {
    root.querySelectorAll("[data-report-explorer]").forEach((explorer) => {
        if (explorer.dataset.reportExplorerReady === "true") {
            return;
        }

        const table = explorer.querySelector("table");
        const body = explorer.querySelector("[data-report-explorer-body]");
        const filterButtons = Array.from(explorer.querySelectorAll("[data-report-filter]"));
        const searchSelector = explorer.dataset.reportSearchSelector || "[data-report-explorer-search]";
        const searchInput =
            reportsScopedElement(root, searchSelector) ||
            (searchSelector ? document.querySelector(searchSelector) : null);
        if (!table || !body || filterButtons.length === 0) {
            return;
        }

        const rows = Array.from(body.querySelectorAll("[data-report-explorer-row]"));
        const activeButton =
            filterButtons.find((button) => button.getAttribute("aria-pressed") === "true") || filterButtons[0];
        const state = { filter: activeButton?.dataset.reportFilter || "all" };
        const filterParam = explorer.dataset.reportFilterParam || "entity_filter";
        const searchParam = explorer.dataset.reportSearchParam || "entity_search";

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
                const rowHref = reportsUrlWithExplorerState(baseRowHref, currentState, filterParam, searchParam);
                row.dataset.rowHref = rowHref;
                row.querySelectorAll("[data-report-link]").forEach((link) => {
                    const baseUrl = link.dataset.baseUrl || link.getAttribute("href") || "";
                    link.setAttribute(
                        "href",
                        reportsUrlWithExplorerState(baseUrl, currentState, filterParam, searchParam)
                    );
                });
            });
        }

        function syncLocationState() {
            if (typeof window.history?.replaceState !== "function") {
                return;
            }
            const nextUrl = reportsUrlWithExplorerState(
                window.location.href,
                explorerState(),
                filterParam,
                searchParam
            );
            window.history.replaceState(window.history.state, "", nextUrl);
        }

        function syncExplorerState({ replaceLocation = true } = {}) {
            syncReportLinks();
            if (replaceLocation) {
                syncLocationState();
            }
        }

        function rowMatchesFilter(row) {
            if (state.filter === "has-income") return row.dataset.hasIncome === "true";
            if (state.filter === "has-spending") return row.dataset.hasSpending === "true";
            if (state.filter === "all") return true;
            return String(row.dataset.filterTokens || "")
                .split(/\s+/)
                .includes(state.filter);
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
            state.filter = button.dataset.reportFilter || "all";
            filterButtons.forEach((filterButton) => {
                const isActive = filterButton === button;
                filterButton.classList.toggle("btn-primary", isActive);
                filterButton.classList.toggle("btn-outline-secondary", !isActive);
                filterButton.setAttribute("aria-pressed", isActive ? "true" : "false");
            });
            applyFilter();
            syncExplorerState();
        }

        explorer.dataset.reportExplorerReady = "true";
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

function setupReportPinButtons(root = document) {
    reportsScopedElements(root, "[data-report-pin-button]").forEach((button) => {
        if (button.dataset.reportPinReady === "true") {
            return;
        }

        const payloadScript = button.parentElement?.querySelector("[data-report-pin-payload]");
        if (!payloadScript) {
            return;
        }

        let payload = {};
        try {
            payload = JSON.parse(payloadScript.textContent || "{}");
        } catch (_error) {
            payload = {};
        }

        function statusElement() {
            let status = button.parentElement?.querySelector("[data-report-pin-status]");
            if (!status) {
                status = document.createElement("span");
                status.className = "reports-pinned-status";
                status.setAttribute("role", "status");
                status.setAttribute("aria-live", "polite");
                status.dataset.reportPinStatus = "true";
                button.insertAdjacentElement("afterend", status);
            }
            return status;
        }

        function showLimitMessage(data) {
            const status = statusElement();
            status.replaceChildren();
            status.append(document.createTextNode(data.message || reportsTranslate("Pinned report limit reached.")));
            if (data.overview_url) {
                status.append(document.createTextNode(" "));
                const overviewLink = document.createElement("a");
                overviewLink.href = data.overview_url;
                overviewLink.textContent = reportsTranslate("Edit pins");
                status.append(overviewLink);
            }
            if (data.settings_url) {
                status.append(document.createTextNode(" "));
                const settingsLink = document.createElement("a");
                settingsLink.href = data.settings_url;
                settingsLink.textContent = reportsTranslate("Settings");
                status.append(settingsLink);
            }
        }

        function markPinned(message) {
            const icon = button.querySelector("[data-report-pin-icon]");
            const label = button.querySelector("[data-report-pin-label]");
            icon?.classList.remove("bi-pin-angle");
            icon?.classList.add("bi-pin-fill");
            if (label) {
                label.textContent = reportsTranslate("Pinned");
            }
            button.disabled = true;
            statusElement().textContent = message || reportsTranslate("Report pinned.");
        }

        button.dataset.reportPinReady = "true";
        button.addEventListener("click", async () => {
            if (!button.dataset.pinUrl) {
                return;
            }
            button.disabled = true;
            try {
                const response = await fetch(button.dataset.pinUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRF-Token": getCsrfToken(),
                        "X-Requested-With": "fetch",
                    },
                    body: JSON.stringify(payload),
                });
                const data = await response.json();
                if (data.ok) {
                    markPinned(data.message);
                    return;
                }
                if (data.limit_reached) {
                    button.disabled = false;
                    showLimitMessage(data);
                    return;
                }
                button.disabled = false;
                statusElement().textContent = data.message || reportsTranslate("Report could not be pinned.");
            } catch (_error) {
                button.disabled = false;
                statusElement().textContent = reportsTranslate("Report could not be pinned.");
            }
        });
    });
}

function setupPinnedReports(root = document) {
    reportsScopedElements(root, "[data-pinned-reports]").forEach((section) => {
        if (section.dataset.pinnedReportsReady === "true") {
            return;
        }

        const list = section.querySelector("[data-pinned-list]");
        const editButton = section.querySelector("[data-pinned-edit-toggle]");
        const saveButton = section.querySelector("[data-pinned-save]");
        const cancelButton = section.querySelector("[data-pinned-cancel]");
        const status = section.querySelector("[data-pinned-status]");
        let snapshot = "";

        function cards() {
            return Array.from(section.querySelectorAll("[data-pinned-card]"));
        }

        function setStatus(message) {
            if (status) {
                status.textContent = message || "";
            }
        }

        function setEditMode(active) {
            section.classList.toggle("is-editing", active);
            cards().forEach((card) => {
                if (!active) {
                    card.classList.remove("is-removing");
                    const toggle = card.querySelector("[data-pinned-remove-toggle]");
                    const icon = card.querySelector("[data-pinned-remove-icon]");
                    const label = card.querySelector("[data-pinned-remove-label]");
                    toggle?.setAttribute("aria-pressed", "false");
                    toggle?.setAttribute("aria-label", reportsTranslate("Unpin report"));
                    if (toggle) {
                        toggle.title = reportsTranslate("Unpin report");
                    }
                    icon?.classList.remove("bi-pin-angle");
                    icon?.classList.add("bi-pin-fill");
                    if (label) {
                        label.textContent = reportsTranslate("Pinned");
                    }
                }
            });
        }

        function moveCard(card, direction) {
            if (!card || !list) {
                return;
            }
            if (direction === "up" && card.previousElementSibling) {
                list.insertBefore(card, card.previousElementSibling);
            }
            if (direction === "down" && card.nextElementSibling) {
                list.insertBefore(card.nextElementSibling, card);
            }
        }

        function toggleRemoval(card) {
            const removing = !card.classList.contains("is-removing");
            card.classList.toggle("is-removing", removing);
            const toggle = card.querySelector("[data-pinned-remove-toggle]");
            const icon = card.querySelector("[data-pinned-remove-icon]");
            const label = card.querySelector("[data-pinned-remove-label]");
            toggle?.setAttribute("aria-pressed", removing ? "true" : "false");
            toggle?.setAttribute(
                "aria-label",
                removing ? reportsTranslate("Keep pinned") : reportsTranslate("Unpin report")
            );
            if (toggle) {
                toggle.title = removing ? reportsTranslate("Keep pinned") : reportsTranslate("Unpin report");
            }
            icon?.classList.toggle("bi-pin-fill", !removing);
            icon?.classList.toggle("bi-pin-angle", removing);
            if (label) {
                label.textContent = removing ? reportsTranslate("Will be unpinned") : reportsTranslate("Pinned");
            }
        }

        async function saveEdits() {
            if (!section.dataset.pinnedSaveUrl) {
                return;
            }
            const pins = cards().map((card, index) => ({
                id: card.dataset.pinId,
                sort_order: index,
                short_title: card.querySelector("[data-pinned-title]")?.value || "",
                remove: card.classList.contains("is-removing"),
            }));

            saveButton.disabled = true;
            try {
                const response = await fetch(section.dataset.pinnedSaveUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRF-Token": getCsrfToken(),
                        "X-Requested-With": "fetch",
                    },
                    body: JSON.stringify({ pins }),
                });
                const data = await response.json();
                if (!data.ok) {
                    setStatus(data.message || reportsTranslate("Pinned reports could not be saved."));
                    saveButton.disabled = false;
                    return;
                }
                if (data.html) {
                    const wrapper = document.createElement("div");
                    wrapper.innerHTML = data.html.trim();
                    const nextSection = wrapper.firstElementChild;
                    if (nextSection) {
                        section.replaceWith(nextSection);
                        window.financeApp?.runInitializers(nextSection);
                        const nextStatus = nextSection.querySelector("[data-pinned-status]");
                        if (nextStatus) {
                            nextStatus.textContent = data.message || reportsTranslate("Pinned reports saved.");
                        }
                    }
                } else {
                    setStatus(data.message || reportsTranslate("Pinned reports saved."));
                    setEditMode(false);
                    saveButton.disabled = false;
                }
            } catch (_error) {
                setStatus(reportsTranslate("Pinned reports could not be saved."));
                saveButton.disabled = false;
            }
        }

        section.dataset.pinnedReportsReady = "true";
        editButton?.addEventListener("click", () => {
            snapshot = list?.innerHTML || "";
            setStatus("");
            setEditMode(true);
        });
        cancelButton?.addEventListener("click", () => {
            if (list) {
                list.innerHTML = snapshot;
            }
            setStatus("");
            setEditMode(false);
        });
        saveButton?.addEventListener("click", saveEdits);

        section.addEventListener("click", (event) => {
            if (!section.classList.contains("is-editing")) {
                return;
            }
            const card = event.target.closest("[data-pinned-card]");
            if (!card) {
                return;
            }
            const moveButton = event.target.closest("[data-pinned-move]");
            if (moveButton) {
                moveCard(card, moveButton.dataset.pinnedMove);
                return;
            }
            if (event.target.closest("[data-pinned-remove-toggle]")) {
                toggleRemoval(card);
            }
        });
    });
}

function setupReportsPage(root = document) {
    root = reportsRoot(root);
    if (!reportsScopedElement(root, "[data-reports-page]")) return;

    if (typeof setupFlatpickrInputs === "function") {
        setupFlatpickrInputs(root);
    }
    setupReportsCustomRange(root);
    setupReportsScopeRefiners(root);
    setupTaxonomyOpenControls(root);
    setupTaxonomyTargetSwitchers(root);
    setupTaxonomyExplorer(root);
    setupReportOpenControls(root);
    setupReportTargetSwitchers(root);
    setupReportExplorers(root);
    setupReportPinButtons(root);
    setupPinnedReports(root);
}

window.financeApp?.registerInitializer("reports.page", setupReportsPage);
window.financeApp?.registerInitializer("reports.pin-buttons", setupReportPinButtons);
window.financeApp?.registerInitializer("reports.pinned", setupPinnedReports);

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setupReportsPage());
} else {
    setupReportsPage();
}
