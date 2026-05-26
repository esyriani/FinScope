function clearDashboardDrilldownSelection(scope, selectedElement) {
    scope.querySelectorAll(".dashboard-drilldown-selected").forEach((element) => {
        if (element !== selectedElement) {
            window.echarts?.getInstanceByDom(element)?.dispatchAction({ type: "downplay" });
            element.classList.remove("dashboard-drilldown-selected");
        }
    });
}

function selectDashboardDrilldownItem(element) {
    const scope = element.closest("[data-dashboard-drilldown-scope]");
    if (!scope) return;

    clearDashboardDrilldownSelection(scope, element);
    element.classList.add("dashboard-drilldown-selected");
}

function dashboardScopedElement(root, selector) {
    return root.matches?.(selector) ? root : root.querySelector(selector);
}

function setupDashboardDrilldownInteractions(root = document) {
    const scope = dashboardScopedElement(root, "[data-dashboard-drilldown-scope]");
    if (!scope) return;

    const drilldownLinks = scope.querySelectorAll(
        ".metric-card[href], .status-strip a[href], .dashboard-grid table tbody a[href]"
    );

    drilldownLinks.forEach((link) => {
        if (link.dataset.dashboardDrilldownReady === "true") return;

        link.dataset.dashboardDrilldownReady = "true";
        link.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            selectDashboardDrilldownItem(link);
        });
        link.addEventListener("dblclick", (event) => {
            event.preventDefault();
            event.stopPropagation();
            window.location.href = link.href;
        });
    });
}

function setupDashboardCustomRange(root = document) {
    const periodSelect = dashboardScopedElement(root, "#dashboard-period");
    if (!periodSelect) return;

    const fields = root.querySelectorAll("[data-dashboard-custom-range]");
    const dateInputs = root.querySelectorAll("[data-dashboard-custom-range] [data-flatpickr-date]");

    const updateVisibility = () => {
        const isCustom = periodSelect.value === "custom";
        fields.forEach((field) => field.classList.toggle("d-none", !isCustom));
        dateInputs.forEach((input) => {
            input.required = isCustom;
        });
    };

    if (periodSelect.dataset.dashboardPeriodReady !== "true") {
        periodSelect.dataset.dashboardPeriodReady = "true";
        periodSelect.addEventListener("change", updateVisibility);
    }
    updateVisibility();
}

function setupDashboardQuickView(root = document) {
    const input = dashboardScopedElement(root, "[data-dashboard-quick-view-input]");
    if (!input) return;

    const buttons = root.querySelectorAll("[data-dashboard-quick-view]");
    const customGroups = Array.from(root.querySelectorAll(
        "[data-dashboard-custom-categories], [data-dashboard-custom-tags]"
    ));
    const customBreak = dashboardScopedElement(root, "[data-dashboard-custom-filter-break]");

    const updateCustomFilters = () => {
        const isCustom = input.value === "custom";

        customBreak?.classList.toggle("d-none", !isCustom);

        customGroups.forEach((group) => {
            group.classList.toggle("d-none", !isCustom);

            const multiselect = group.querySelector("[data-tag-multiselect]");
            if (multiselect) {
                multiselect.dataset.disabled = isCustom ? "false" : "true";
            }

            const toggle = group.querySelector("[data-tag-multiselect-toggle]");
            if (toggle) {
                toggle.setAttribute("aria-disabled", isCustom ? "false" : "true");
                toggle.setAttribute("tabindex", isCustom ? "0" : "-1");
            }

            const menu = group.querySelector("[data-tag-multiselect-menu]");
            if (!isCustom) {
                if (toggle) {
                    toggle.setAttribute("aria-expanded", "false");
                }
                if (menu) {
                    menu.style.display = "none";
                }
            }

            group.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
                checkbox.disabled = !isCustom;
            });
        });
    };

    const updateButtons = () => {
        buttons.forEach((button) => {
            const isActive = button.dataset.dashboardQuickView === input.value;
            button.classList.toggle("btn-primary", isActive);
            button.classList.toggle("btn-outline-secondary", !isActive);
            button.setAttribute("aria-pressed", isActive ? "true" : "false");
        });
    };

    buttons.forEach((button) => {
        if (button.dataset.dashboardQuickViewReady === "true") {
            return;
        }

        button.dataset.dashboardQuickViewReady = "true";
        button.addEventListener("click", (event) => {
            const nextView = button.dataset.dashboardQuickView || "all";
            input.value = nextView;
            updateCustomFilters();
            updateButtons();

            if (nextView === "custom") {
                event.preventDefault();
                customGroups[0]?.querySelector("[data-tag-multiselect-toggle]")?.focus();
            }
        });
    });

    updateCustomFilters();
    updateButtons();
}

function setupDashboardQualityPanel(root = document) {
    const button = dashboardScopedElement(root, "[data-dashboard-quality-toggle]");
    if (!button || button.dataset.dashboardQualityReady === "true") return;

    const targetSelector = button.getAttribute("data-bs-target");
    const collapseElement = targetSelector ? root.querySelector(targetSelector) || document.querySelector(targetSelector) : null;
    if (!collapseElement) return;

    button.dataset.dashboardQualityReady = "true";
    const showLabel = button.dataset.showLabel || "More";
    const hideLabel = button.dataset.hideLabel || "Less";
    collapseElement.addEventListener("show.bs.collapse", () => {
        button.textContent = hideLabel;
    });
    collapseElement.addEventListener("hide.bs.collapse", () => {
        button.textContent = showLabel;
    });
}

function setupDashboardPage(root = document) {
    if (!dashboardScopedElement(root, "[data-dashboard-drilldown-scope]")) {
        return;
    }

    if (typeof setupFlatpickrInputs === "function") {
        setupFlatpickrInputs(root);
    }
    setupDashboardDrilldownInteractions(root);
    setupDashboardCustomRange(root);
    setupDashboardQuickView(root);
    setupDashboardQualityPanel(root);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupDashboardPage);
} else {
    setupDashboardPage();
}

window.setupDashboardPage = setupDashboardPage;
