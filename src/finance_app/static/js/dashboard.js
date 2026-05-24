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

function setupDashboardDrilldownInteractions() {
    const scope = document.querySelector("[data-dashboard-drilldown-scope]");
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

function setupDashboardCustomRange() {
    const periodSelect = document.getElementById("dashboard-period");
    if (!periodSelect) return;

    const fields = document.querySelectorAll("[data-dashboard-custom-range]");
    const dateInputs = document.querySelectorAll("[data-dashboard-custom-range] [data-flatpickr-date]");

    const updateVisibility = () => {
        const isCustom = periodSelect.value === "custom";
        fields.forEach((field) => field.classList.toggle("d-none", !isCustom));
        dateInputs.forEach((input) => {
            input.required = isCustom;
        });
    };

    periodSelect.addEventListener("change", updateVisibility);
    updateVisibility();
}

function setupDashboardQuickView() {
    const input = document.querySelector("[data-dashboard-quick-view-input]");
    if (!input) return;

    const buttons = document.querySelectorAll("[data-dashboard-quick-view]");
    const customGroups = Array.from(document.querySelectorAll(
        "[data-dashboard-custom-categories], [data-dashboard-custom-tags]"
    ));
    const customBreak = document.querySelector("[data-dashboard-custom-filter-break]");

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

function setupDashboardPage() {
    if (typeof setupFlatpickrInputs === "function") {
        setupFlatpickrInputs();
    }
    setupDashboardCustomRange();
    setupDashboardQuickView();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupDashboardPage);
} else {
    setupDashboardPage();
}
