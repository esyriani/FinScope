function setupUploadAccountBehavior() {
    const form = document.querySelector("[data-upload-form]");
    const statementTypeSelect = form?.querySelector("[data-statement-type-select]");
    const accountTypeSelect = form?.querySelector("[data-account-type-select]");
    const paidFromField = form?.querySelector("[data-paid-from-field]");
    const interacDirectionField = form?.querySelector("[data-interac-direction-field]");
    const interacDirectionSelect = form?.querySelector('select[name="interac_direction"]');
    const interacWarning = form?.querySelector("[data-interac-warning]");

    if (!form || !statementTypeSelect || !accountTypeSelect || !paidFromField || !interacDirectionField || !interacDirectionSelect || !interacWarning) {
        return;
    }

    if (form.dataset.uploadAccountBehaviorReady === "true") {
        return;
    }

    form.dataset.uploadAccountBehaviorReady = "true";

    const updateFromStatementType = () => {
        const selectedOption = statementTypeSelect.selectedOptions[0];
        const defaultAccountType = selectedOption?.dataset.accountType || "checking";
        // Statement import type chooses parser/import behavior; account
        // reporting role controls report classification. Keep the role aligned
        // with the selected type by default, while still allowing overrides.
        if (accountTypeSelect.value !== defaultAccountType) {
            accountTypeSelect.value = defaultAccountType;
        }
        updatePaidFromVisibility();
        updateInteracDirectionVisibility();
    };

    const updatePaidFromVisibility = () => {
        const showPaidFrom = accountTypeSelect.value === "credit_card";
        paidFromField.classList.toggle("d-none", !showPaidFrom);
    };

    const updateInteracDirectionVisibility = () => {
        const selectedOption = statementTypeSelect.selectedOptions[0];
        const showDirection = selectedOption?.dataset.parserType === "interac_etransfer";
        interacDirectionField.classList.toggle("d-none", !showDirection);
        interacWarning.classList.toggle("d-none", !showDirection);
        if (!showDirection) {
            interacDirectionSelect.value = "auto";
        }
    };

    statementTypeSelect.addEventListener("change", updateFromStatementType);
    accountTypeSelect.addEventListener("change", updatePaidFromVisibility);
    updateFromStatementType();
}

setupUploadAccountBehavior();
