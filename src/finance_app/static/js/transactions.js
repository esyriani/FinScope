function transactionsScopedElement(root, selector) {
    return root.matches?.(selector) ? root : root.querySelector(selector);
}

function transactionsRoot(root) {
    return root && typeof root.querySelector === "function" ? root : document;
}

function setupTransactionsCustomRange(root = document) {
    const periodSelect = transactionsScopedElement(root, "#period");
    if (!periodSelect) return;

    const fields = root.querySelectorAll("[data-transactions-custom-range]");
    const dateInputs = root.querySelectorAll("[data-transactions-custom-range] [data-flatpickr-date]");

    const updateVisibility = () => {
        const isCustom = periodSelect.value === "custom";
        fields.forEach((field) => field.classList.toggle("d-none", !isCustom));
        dateInputs.forEach((input) => {
            input.required = isCustom;
        });
    };

    if (periodSelect.dataset.transactionsPeriodReady !== "true") {
        periodSelect.dataset.transactionsPeriodReady = "true";
        periodSelect.addEventListener("change", updateVisibility);
    }
    updateVisibility();
}

function setupTransactionsPage(root = document) {
    root = transactionsRoot(root);
    if (!transactionsScopedElement(root, "[data-transactions-page]")) {
        return;
    }

    if (typeof setupFlatpickrInputs === "function") {
        setupFlatpickrInputs(root);
    }
    setupTransactionsCustomRange(root);
}

window.financeApp?.registerInitializer("transactions.page", setupTransactionsPage);

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setupTransactionsPage());
} else {
    setupTransactionsPage();
}
