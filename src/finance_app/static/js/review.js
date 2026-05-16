function setupReviewTransactionSelectors(root = document) {
    const selectors = Array.from(root.querySelectorAll("[data-review-transaction-selector]"));

    selectors.forEach((selector) => {
        if (selector.dataset.reviewSelectorReady === "true") {
            return;
        }

        selector.dataset.reviewSelectorReady = "true";
        const form = selector.closest("form");
        const checkboxes = Array.from(selector.querySelectorAll("[data-review-transaction-checkbox]"));
        const status = selector.querySelector("[data-review-selection-status]");
        const submitLabel = form?.querySelector("[data-review-submit-label]");
        const defaultSubmitLabel = submitLabel?.textContent || "";
        const saveRule = form?.querySelector("input[name='create_rule'][value='1']");
        const noRule = form?.querySelector("input[name='create_rule'][value='0']");

        if (!form || checkboxes.length === 0 || !noRule) {
            return;
        }

        function syncRuleChoice() {
            const selectedCount = checkboxes.filter((checkbox) => checkbox.checked).length;
            const hasSelection = selectedCount > 0;

            if (hasSelection) {
                noRule.checked = true;
            }

            if (saveRule) {
                saveRule.disabled = hasSelection;
            }

            if (status) {
                status.textContent = hasSelection
                    ? financeTranslate(
                        "{count} selected. The category will apply only to selected transactions.",
                        { count: selectedCount }
                    )
                    : financeTranslate("No transactions selected. The category will apply to the whole group.");
            }

            if (submitLabel) {
                submitLabel.textContent = hasSelection
                    ? financeTranslate("Categorize selected ({count})", { count: selectedCount })
                    : defaultSubmitLabel;
            }
        }

        checkboxes.forEach((checkbox) => {
            checkbox.addEventListener("change", syncRuleChoice);
        });
        syncRuleChoice();
    });
}

setupReviewTransactionSelectors();
