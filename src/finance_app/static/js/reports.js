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

function setupReportsPage(root = document) {
    root = reportsRoot(root);
    if (!reportsScopedElement(root, "[data-reports-page]")) return;

    if (typeof setupFlatpickrInputs === "function") {
        setupFlatpickrInputs(root);
    }
    setupReportsCustomRange(root);
}

window.financeApp?.registerInitializer("reports.page", setupReportsPage);

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setupReportsPage());
} else {
    setupReportsPage();
}
