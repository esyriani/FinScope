function dashboardScopedElement(root, selector) {
    return root.matches?.(selector) ? root : root.querySelector(selector);
}

function dashboardRoot(root) {
    return root && typeof root.querySelector === "function" ? root : document;
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

function setupDashboardPage(root = document) {
    root = dashboardRoot(root);
    if (!dashboardScopedElement(root, "#dashboard-page")) {
        return;
    }

    if (typeof setupFlatpickrInputs === "function") {
        setupFlatpickrInputs(root);
    }
    setupDashboardCustomRange(root);
}

window.financeApp?.registerInitializer("dashboard.page", setupDashboardPage);

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setupDashboardPage());
} else {
    setupDashboardPage();
}
