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
                    ? financeTranslate("{count} selected. The category will apply only to selected transactions.", {
                          count: selectedCount,
                      })
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

const reviewTerminalJobStatuses = new Set(["completed", "failed", "cancelled"]);
const reviewTrackedJobStatusUrls = new Set();

function delayReviewRefresh(milliseconds) {
    return new Promise((resolve) => {
        window.setTimeout(resolve, milliseconds);
    });
}

async function fetchReviewJobStatus(statusUrl) {
    const response = await fetch(statusUrl, {
        headers: {
            "X-Requested-With": "fetch",
        },
        credentials: "same-origin",
    });

    if (!response.ok) {
        throw new Error("Review job status could not be loaded.");
    }

    return response.json();
}

async function refreshReviewAfterJob(statusUrl, refreshUrl, selector) {
    if (!statusUrl || !refreshUrl || !selector || reviewTrackedJobStatusUrls.has(statusUrl)) {
        return;
    }

    reviewTrackedJobStatusUrls.add(statusUrl);
    try {
        for (let attempt = 0; attempt < 120; attempt += 1) {
            const job = await fetchReviewJobStatus(statusUrl);
            if (reviewTerminalJobStatuses.has(job.status)) {
                await window.ajaxRefreshFromUrl?.(refreshUrl, selector);
                return;
            }
            await delayReviewRefresh(750);
        }
    } catch (_error) {
        return;
    } finally {
        reviewTrackedJobStatusUrls.delete(statusUrl);
    }
}

function setupReviewApplyAjaxRefresh() {
    if (document.documentElement.dataset.reviewApplyAjaxRefreshReady === "true") {
        return;
    }

    document.documentElement.dataset.reviewApplyAjaxRefreshReady = "true";
    document.addEventListener("finance:ajax-action-complete", (event) => {
        const detail = event.detail || {};
        if (!detail.form?.matches?.("[data-review-apply-form]")) {
            return;
        }

        refreshReviewAfterJob(
            detail.data?.job_status_url,
            detail.data?.refresh_url || detail.refreshUrl || window.location.href,
            detail.selector
        );
    });
}

window.financeApp?.registerInitializer("review.transaction-selectors", setupReviewTransactionSelectors);

setupReviewApplyAjaxRefresh();
setupReviewTransactionSelectors();
