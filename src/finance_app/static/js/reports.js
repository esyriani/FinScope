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

const taxonomyExplorerConfig = {
    controlSelector: "[data-taxonomy-open-control]",
    openReadyKey: "taxonomyOpenReady",
    openInputSelector: "[data-taxonomy-open-input]",
    openMenuSelector: "[data-taxonomy-open-menu]",
    openOptionsSelector: "[data-taxonomy-open-options]",
    openOptionKey: "taxonomyOpenOption",
    openOptionSelector: "[data-taxonomy-open-option]",
    noResultsText: "No categories or tags found.",
    switcherSelector: "[data-taxonomy-target-switcher]",
    switcherReadyKey: "taxonomySwitcherReady",
    switcherSearchSelector: "[data-taxonomy-target-search]",
    switcherOptionSelector: "[data-taxonomy-target-option]",
    explorerSelector: "[data-taxonomy-explorer]",
    explorerReadyKey: "taxonomyExplorerReady",
    explorerBodySelector: "[data-taxonomy-explorer-body]",
    explorerRowSelector: "[data-taxonomy-explorer-row]",
    filterSelector: "[data-taxonomy-filter]",
    filterKey: "taxonomyFilter",
    linkSelector: "[data-taxonomy-report-link]",
    searchSelector: "[data-taxonomy-explorer-search]",
    filterParam: "taxonomy_filter",
    searchParam: "taxonomy_search",
    rowMatchesFilter(row, filter) {
        if (filter === "categories") return row.dataset.kind === "category";
        if (filter === "tags") return row.dataset.kind === "tag";
        if (filter === "analytics-categories") return row.dataset.analyticsCategory === "true";
        if (filter === "has-income") return row.dataset.hasIncome === "true";
        if (filter === "has-spending") return row.dataset.hasSpending === "true";
        return true;
    },
};

const reportExplorerConfig = {
    controlSelector: "[data-report-open-control]",
    openReadyKey: "reportOpenReady",
    openInputSelector: "[data-report-open-input]",
    openMenuSelector: "[data-report-open-menu]",
    openOptionsSelector: "[data-report-open-options]",
    openOptionKey: "reportOpenOption",
    openOptionSelector: "[data-report-open-option]",
    noResultsDatasetKey: "noResultsText",
    noResultsText: "No report targets found.",
    openFilterParamDatasetKey: "reportOpenFilterParam",
    openSearchParamDatasetKey: "reportOpenSearchParam",
    openSearchSelectorDatasetKey: "reportOpenSearchSelector",
    switcherSelector: "[data-report-target-switcher]",
    switcherReadyKey: "reportSwitcherReady",
    switcherSearchSelector: "[data-report-target-search]",
    switcherOptionSelector: "[data-report-target-option]",
    explorerSelector: "[data-report-explorer]",
    explorerReadyKey: "reportExplorerReady",
    explorerBodySelector: "[data-report-explorer-body]",
    explorerRowSelector: "[data-report-explorer-row]",
    filterSelector: "[data-report-filter]",
    filterKey: "reportFilter",
    linkSelector: "[data-report-link]",
    searchSelector: "[data-report-explorer-search]",
    searchSelectorDatasetKey: "reportSearchSelector",
    filterParam: "entity_filter",
    searchParam: "entity_search",
    filterParamDatasetKey: "reportFilterParam",
    searchParamDatasetKey: "reportSearchParam",
    rowMatchesFilter(row, filter) {
        if (filter === "has-income") return row.dataset.hasIncome === "true";
        if (filter === "has-spending") return row.dataset.hasSpending === "true";
        if (filter === "all") return true;
        return String(row.dataset.filterTokens || "")
            .split(/\s+/)
            .includes(filter);
    },
};

function reportsDatasetValue(element, key) {
    return key ? element?.dataset[key] || "" : "";
}

const reportsOpenControlCloseEvent = "finance:reports-open-control-close";
const reportsOpenControlSelector = `${taxonomyExplorerConfig.controlSelector}, ${reportExplorerConfig.controlSelector}`;

function reportsOpenControlForEvent(event) {
    return typeof event.target?.closest === "function" ? event.target.closest(reportsOpenControlSelector) : null;
}

function setupReportsOpenControlGlobalListeners() {
    if (window.financeReportsOpenControlsGlobalReady === "true") {
        return;
    }

    window.financeReportsOpenControlsGlobalReady = "true";
    document.addEventListener("click", (event) => {
        const currentControl = reportsOpenControlForEvent(event);
        document.querySelectorAll(reportsOpenControlSelector).forEach((control) => {
            if (control !== currentControl) {
                control.dispatchEvent(new CustomEvent(reportsOpenControlCloseEvent));
            }
        });
    });
}

function reportsExplorerParams(source, config, prefix = "") {
    const filterDatasetKey = prefix ? `${prefix}FilterParamDatasetKey` : "filterParamDatasetKey";
    const searchDatasetKey = prefix ? `${prefix}SearchParamDatasetKey` : "searchParamDatasetKey";
    return {
        filterParam: reportsDatasetValue(source, config[filterDatasetKey]) || config.filterParam,
        searchParam: reportsDatasetValue(source, config[searchDatasetKey]) || config.searchParam,
    };
}

function reportsExplorerSearchInput(root, config, explorer = null, source = null) {
    const searchSelector =
        reportsDatasetValue(source, config.openSearchSelectorDatasetKey) ||
        reportsDatasetValue(explorer, config.searchSelectorDatasetKey) ||
        config.searchSelector;
    return (
        (searchSelector ? reportsScopedElement(root, searchSelector) : null) ||
        (searchSelector ? document.querySelector(searchSelector) : null)
    );
}

function currentReportsExplorerState(root, config, source = null) {
    const explorer = reportsScopedElement(root, config.explorerSelector);
    const searchInput = reportsExplorerSearchInput(root, config, explorer, source);
    const activeFilter = explorer?.querySelector(`${config.filterSelector}[aria-pressed='true']`);
    return {
        filter: activeFilter?.dataset[config.filterKey] || "all",
        search: searchInput?.value || "",
    };
}

function reportsOpenControlNoResultsText(control, config) {
    return reportsTranslate(reportsDatasetValue(control, config.noResultsDatasetKey) || config.noResultsText);
}

function setupReportsOpenControls(root, config) {
    root.querySelectorAll(config.controlSelector).forEach((control) => {
        if (control.dataset[config.openReadyKey] === "true") {
            return;
        }

        const input = control.querySelector(config.openInputSelector);
        const menu = control.querySelector(config.openMenuSelector);
        const optionsScript = control.querySelector(config.openOptionsSelector);
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

        control.dataset[config.openReadyKey] = "true";
        let activeIndex = -1;
        let suggestions = [];
        let debounceId = 0;
        const suggestionsLimit = Math.max(1, Number(control.dataset.suggestionsLimit || 8) || 8);
        const { filterParam, searchParam } = reportsExplorerParams(control, config, "open");
        const noResultsText = reportsOpenControlNoResultsText(control, config);

        function reportsOpenOptionId(index) {
            return `${menu.id || input.id || "reports-open"}-option-${index}`;
        }

        function setActiveDescendant(activeOption) {
            if (activeOption?.id) {
                input.setAttribute("aria-activedescendant", activeOption.id);
            } else {
                input.removeAttribute("aria-activedescendant");
            }
        }

        function setExpanded(expanded) {
            input.setAttribute("aria-expanded", expanded ? "true" : "false");
            menu.hidden = !expanded;
            if (!expanded) {
                setActiveDescendant(null);
            }
        }

        function clearMenu() {
            suggestions = [];
            activeIndex = -1;
            menu.replaceChildren();
            setExpanded(false);
        }

        function renderStatus(message) {
            setActiveDescendant(null);
            const status = document.createElement("div");
            status.className = "merchant-autocomplete-status";
            status.setAttribute("role", "option");
            status.setAttribute("aria-disabled", "true");
            status.textContent = message;
            menu.replaceChildren(status);
            setExpanded(true);
        }

        function updateActiveOption() {
            const options = Array.from(menu.querySelectorAll(config.openOptionSelector));
            let activeOption = null;
            options.forEach((option, index) => {
                const active = index === activeIndex;
                option.classList.toggle("active", active);
                option.setAttribute("aria-selected", active ? "true" : "false");
                if (active) {
                    activeOption = option;
                }
            });
            setActiveDescendant(activeOption);
        }

        function targetUrl(target) {
            return reportsUrlWithExplorerState(
                target.url || "",
                currentReportsExplorerState(root, config, control),
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
            setActiveDescendant(null);
            if (!items.length) {
                renderStatus(noResultsText);
                return;
            }

            items.forEach((target, index) => {
                const option = document.createElement("button");
                option.type = "button";
                option.tabIndex = -1;
                option.className = "merchant-autocomplete-option reports-taxonomy-open-option";
                option.id = reportsOpenOptionId(index);
                option.setAttribute("role", "option");
                option.setAttribute("aria-selected", "false");
                option.dataset[config.openOptionKey] = "true";

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

        control.addEventListener(reportsOpenControlCloseEvent, clearMenu);
    });
}

function setupReportsTargetSwitchers(root, config) {
    root.querySelectorAll(config.switcherSelector).forEach((switcher) => {
        if (switcher.dataset[config.switcherReadyKey] === "true") {
            return;
        }

        const search = switcher.querySelector(config.switcherSearchSelector);
        const options = Array.from(switcher.querySelectorAll(config.switcherOptionSelector));
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

        switcher.dataset[config.switcherReadyKey] = "true";
        search.addEventListener("input", applySearch);
        switcher.addEventListener("shown.bs.dropdown", () => {
            search.focus();
            search.select();
        });
        applySearch();
    });
}

function setupReportsExplorer(root, config) {
    root.querySelectorAll(config.explorerSelector).forEach((explorer) => {
        if (explorer.dataset[config.explorerReadyKey] === "true") {
            return;
        }

        const table = explorer.querySelector("table");
        const body = explorer.querySelector(config.explorerBodySelector);
        const filterButtons = Array.from(explorer.querySelectorAll(config.filterSelector));
        const searchInput = reportsExplorerSearchInput(root, config, explorer);
        if (!table || !body || filterButtons.length === 0) {
            return;
        }

        const rows = Array.from(body.querySelectorAll(config.explorerRowSelector));
        const activeButton =
            filterButtons.find((button) => button.getAttribute("aria-pressed") === "true") || filterButtons[0];
        const state = { filter: activeButton?.dataset[config.filterKey] || "all" };
        const { filterParam, searchParam } = reportsExplorerParams(explorer, config);

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
                row.dataset.rowHref = reportsUrlWithExplorerState(baseRowHref, currentState, filterParam, searchParam);
                row.querySelectorAll(config.linkSelector).forEach((link) => {
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

        function rowMatchesSearch(row) {
            const query = normalizeReportsSearchText(searchInput?.value || "");
            return !query || normalizeReportsSearchText(row.dataset.searchText).includes(query);
        }

        function applyFilter() {
            rows.forEach((row) => {
                if (config.rowMatchesFilter(row, state.filter) && rowMatchesSearch(row)) {
                    delete row.dataset.tableFilteredOut;
                } else {
                    row.dataset.tableFilteredOut = "true";
                }
            });
            table.dispatchEvent(new CustomEvent("finance:table-filtered"));
        }

        function setActiveFilter(button) {
            state.filter = button.dataset[config.filterKey] || "all";
            filterButtons.forEach((filterButton) => {
                const isActive = filterButton === button;
                filterButton.classList.toggle("btn-primary", isActive);
                filterButton.classList.toggle("btn-outline-secondary", !isActive);
                filterButton.setAttribute("aria-pressed", isActive ? "true" : "false");
            });
            applyFilter();
            syncExplorerState();
        }

        explorer.dataset[config.explorerReadyKey] = "true";
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
    setupReportsOpenControls(root, taxonomyExplorerConfig);
    setupReportsTargetSwitchers(root, taxonomyExplorerConfig);
    setupReportsExplorer(root, taxonomyExplorerConfig);
    setupReportsOpenControls(root, reportExplorerConfig);
    setupReportsTargetSwitchers(root, reportExplorerConfig);
    setupReportsExplorer(root, reportExplorerConfig);
    setupReportPinButtons(root);
    setupPinnedReports(root);
}

window.financeApp?.registerInitializer("reports.page", setupReportsPage);
window.financeApp?.registerInitializer("reports.pin-buttons", setupReportPinButtons);
window.financeApp?.registerInitializer("reports.pinned", setupPinnedReports);
setupReportsOpenControlGlobalListeners();

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setupReportsPage());
} else {
    setupReportsPage();
}
