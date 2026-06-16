(function () {
    const activeTabStorageKey = "finance.reimbursements.activeTab";
    const restoreTabStorageKey = "finance.reimbursements.restoreTab";
    const tabSelector = ".reimbursement-tabs [data-bs-toggle='tab']";

    function storageSet(key, value) {
        try {
            window.sessionStorage?.setItem(key, value);
        } catch (_error) {
            // Session storage can be unavailable in private or locked-down contexts.
        }
    }

    function storageGet(key) {
        try {
            return window.sessionStorage?.getItem(key) || "";
        } catch (_error) {
            return "";
        }
    }

    function storageRemove(key) {
        try {
            window.sessionStorage?.removeItem(key);
        } catch (_error) {
            // Ignore unavailable session storage.
        }
    }

    function escapeId(value) {
        if (window.CSS?.escape) {
            return CSS.escape(value);
        }
        return String(value || "").replaceAll('"', '\\"');
    }

    function money(value) {
        if (window.financeFormatMoney) {
            return window.financeFormatMoney(value);
        }
        return Number(value || 0).toFixed(2);
    }

    function parseAmount(value) {
        const parsed = Number.parseFloat(String(value || "").replace(",", "."));
        return Number.isFinite(parsed) ? parsed : 0;
    }

    function setupMatchForm(form) {
        if (form.dataset.reimbursementMatchReady === "true") {
            return;
        }
        form.dataset.reimbursementMatchReady = "true";

        const reimbursementRemaining = parseAmount(form.dataset.reimbursementRemaining);
        const total = form.querySelector("[data-match-total]");
        const remaining = form.querySelector("[data-match-remaining]");
        const error = form.querySelector("[data-match-error]");
        const submit = form.querySelector("[data-match-submit]");
        const candidates = Array.from(form.querySelectorAll("[data-match-candidate]"));

        function updateReview() {
            let selectedTotal = 0;
            let selectedCount = 0;

            candidates.forEach((candidate) => {
                const checkbox = candidate.querySelector("[data-match-checkbox]");
                const amount = candidate.querySelector("[data-match-amount]");
                if (checkbox?.checked) {
                    selectedCount += 1;
                    selectedTotal += parseAmount(amount?.value);
                }
            });

            const remainingAfterMatch = reimbursementRemaining - selectedTotal;
            const exceedsRemaining = remainingAfterMatch < -0.005;

            if (total) {
                total.textContent = money(selectedTotal);
            }
            if (remaining) {
                remaining.textContent = money(Math.max(remainingAfterMatch, 0));
            }
            if (error) {
                error.hidden = !exceedsRemaining;
            }
            if (submit) {
                submit.disabled = selectedCount === 0 || exceedsRemaining;
            }
        }

        candidates.forEach((candidate) => {
            candidate.querySelector("[data-match-checkbox]")?.addEventListener("change", updateReview);
            candidate.querySelector("[data-match-amount]")?.addEventListener("input", updateReview);
        });
        updateReview();
    }

    function setupReimbursementMatches(root = document) {
        root.querySelectorAll("[data-reimbursement-match-form]").forEach(setupMatchForm);
    }

    function activeReimbursementTab(root = document) {
        return root.querySelector(`${tabSelector}.active`) || document.querySelector(`${tabSelector}.active`);
    }

    function persistActiveReimbursementTab(root = document) {
        const activeTab = activeReimbursementTab(root);
        if (activeTab?.id) {
            storageSet(activeTabStorageKey, activeTab.id);
        }
    }

    function restoreActiveReimbursementTab(root = document) {
        if (storageGet(restoreTabStorageKey) !== "1") {
            return;
        }
        storageRemove(restoreTabStorageKey);

        const tabId = storageGet(activeTabStorageKey);
        if (!tabId) {
            return;
        }

        const tabButton = root.querySelector(`#${escapeId(tabId)}`) || document.getElementById(tabId);
        if (!tabButton) {
            return;
        }

        if (window.bootstrap?.Tab) {
            window.bootstrap.Tab.getOrCreateInstance(tabButton).show();
        } else {
            tabButton.click();
        }
    }

    function setupReimbursementTabPersistence(root = document) {
        root.querySelectorAll(tabSelector).forEach((tabButton) => {
            if (tabButton.dataset.reimbursementTabReady === "true") {
                return;
            }

            tabButton.dataset.reimbursementTabReady = "true";
            tabButton.addEventListener("shown.bs.tab", () => {
                if (tabButton.id) {
                    storageSet(activeTabStorageKey, tabButton.id);
                }
            });
        });

        root.querySelectorAll("[data-ajax-refresh-form]").forEach((form) => {
            if (form.dataset.reimbursementTabRestoreReady === "true") {
                return;
            }

            form.dataset.reimbursementTabRestoreReady = "true";
            form.addEventListener(
                "submit",
                () => {
                    persistActiveReimbursementTab(root);
                    storageSet(restoreTabStorageKey, "1");
                },
                { capture: true }
            );
        });

        restoreActiveReimbursementTab(root);
    }

    window.financeApp.registerInitializer("reimbursements.matches", setupReimbursementMatches);
    window.financeApp.registerInitializer("reimbursements.tabs", setupReimbursementTabPersistence);
    setupReimbursementMatches();
    setupReimbursementTabPersistence();
})();
