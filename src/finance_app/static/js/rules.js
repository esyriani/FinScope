function setupRuleSaveModeControls(root = document) {
    const controls = Array.from(root.querySelectorAll("[data-rule-save-mode-control]"));

    controls.forEach((control) => {
        if (control.dataset.ruleSaveModeReady === "true") {
            return;
        }

        control.dataset.ruleSaveModeReady = "true";
        const form = control.closest("form");
        const saveValue = control.dataset.ruleSaveValue || "";
        const modeInputs = Array.from(control.querySelectorAll("[data-rule-save-mode]"));
        const ruleOnlySections = form
            ? Array.from(form.querySelectorAll("[data-rule-save-only]"))
            : [];

        if (!form || !saveValue || !modeInputs.length || !ruleOnlySections.length) {
            return;
        }

        function selectedValue() {
            return modeInputs.find((input) => input.checked)?.value || "";
        }

        function syncRuleOnlySections() {
            const isSavingRule = selectedValue() === saveValue;

            ruleOnlySections.forEach((section) => {
                section.toggleAttribute("hidden", !isSavingRule);
                section.querySelectorAll("input, select, textarea, button").forEach((input) => {
                    input.disabled = !isSavingRule;
                });
            });
        }

        modeInputs.forEach((input) => input.addEventListener("change", syncRuleOnlySections));
        syncRuleOnlySections();
    });
}

setupRuleSaveModeControls();
window.setupRuleSaveModeControls = setupRuleSaveModeControls;

function setupRuleAmountControls(root = document) {
    const controls = Array.from(root.querySelectorAll("[data-rule-amount-control]"));

    controls.forEach((control) => {
        if (control.dataset.ruleAmountReady === "true") {
            return;
        }

        control.dataset.ruleAmountReady = "true";
        const anyAmount = control.querySelector("[data-rule-any-amount]");
        const body = control.querySelector("[data-rule-amount-body]");
        const modeInputs = Array.from(control.querySelectorAll("[data-rule-amount-mode]"));
        const exactFields = control.querySelector("[data-rule-exact-fields]");
        const rangeFields = control.querySelector("[data-rule-range-fields]");
        const exactAmount = control.querySelector("[data-rule-exact-amount]");
        const rangeMin = control.querySelector("[data-rule-range-min]");
        const rangeMax = control.querySelector("[data-rule-range-max]");
        const hiddenMin = control.querySelector("[data-rule-amount-min]");
        const hiddenMax = control.querySelector("[data-rule-amount-max]");

        if (!anyAmount || !body || !hiddenMin || !hiddenMax) {
            return;
        }

        function selectedMode() {
            return modeInputs.find((input) => input.checked)?.value || "exact";
        }

        function syncHiddenValues() {
            if (anyAmount.checked) {
                hiddenMin.value = "";
                hiddenMax.value = "";
                return;
            }

            if (selectedMode() === "range") {
                hiddenMin.value = rangeMin?.value.trim() || "";
                hiddenMax.value = rangeMax?.value.trim() || "";
                return;
            }

            const exactValue = exactAmount?.value.trim() || "";
            hiddenMin.value = exactValue;
            hiddenMax.value = exactValue;
        }

        function syncDisplay() {
            const isAny = anyAmount.checked;
            const isRange = selectedMode() === "range";

            control.classList.toggle("is-any", isAny);
            body.toggleAttribute("hidden", isAny);
            exactFields?.toggleAttribute("hidden", isAny || isRange);
            rangeFields?.toggleAttribute("hidden", isAny || !isRange);

            modeInputs.forEach((input) => {
                input.disabled = isAny;
            });
            [exactAmount, rangeMin, rangeMax].forEach((input) => {
                if (input) {
                    input.disabled = isAny;
                }
            });

            syncHiddenValues();
        }

        anyAmount.addEventListener("change", syncDisplay);
        modeInputs.forEach((input) => input.addEventListener("change", syncDisplay));
        [exactAmount, rangeMin, rangeMax].forEach((input) => {
            input?.addEventListener("input", syncHiddenValues);
        });
        control.closest("form")?.addEventListener("submit", syncHiddenValues);

        syncDisplay();
    });
}

setupRuleAmountControls();

function setupRulePreviewForms(root = document) {
    const forms = Array.from(root.querySelectorAll("[data-rule-editor]"));

    forms.forEach((form) => {
        if (form.dataset.rulePreviewReady === "true") {
            return;
        }

        form.dataset.rulePreviewReady = "true";
        const preview = form.querySelector("[data-rule-preview]");
        const status = form.querySelector("[data-rule-preview-status]");
        const list = form.querySelector("[data-rule-preview-list]");
        const refresh = form.querySelector("[data-rule-preview-refresh]");
        const url = form.getAttribute("data-rule-preview-url");
        let timer = null;
        let requestId = 0;

        if (!preview || !status || !list || !url) {
            return;
        }

        function renderEmpty(message) {
            status.textContent = message;
            list.innerHTML = "";
        }

        function renderPreview(data) {
            const count = Number(data.match_count || 0);
            status.textContent = financeTranslate(
                count === 1 ? "{count} active matching transaction." : "{count} active matching transactions.",
                { count }
            );

            if (!data.transactions || data.transactions.length === 0) {
                list.innerHTML = `<div class="rule-preview-empty">${escapeHtml(financeTranslate("No active transactions match this rule."))}</div>`;
                return;
            }

            list.innerHTML = data.transactions.map((tx) => `
                <div class="rule-preview-row">
                    <div>
                        <div class="rule-preview-description">${escapeHtml(tx.description)}</div>
                        <div class="rule-preview-meta">${escapeHtml(tx.tx_date)} - ${escapeHtml(tx.current_category || "UNKNOWN")}</div>
                    </div>
                    <strong>${escapeHtml(tx.amount_display)}</strong>
                </div>
            `).join("");
        }

        async function updatePreview() {
            const keyword = form.querySelector("[name='keyword']")?.value.trim() || "";
            if (!keyword) {
                renderEmpty(financeTranslate("Enter a keyword to preview matches."));
                return;
            }

            const currentRequest = ++requestId;
            status.textContent = financeTranslate("Loading preview...");

            try {
                const response = await fetch(url, {
                    method: "POST",
                    body: new FormData(form),
                    headers: {
                        "X-Requested-With": "fetch",
                        "X-CSRF-Token": getCsrfToken(),
                    },
                });
                const data = await response.json();

                if (currentRequest !== requestId) {
                    return;
                }

                if (!response.ok || data.ok === false) {
                    renderEmpty(data.message || financeTranslate("Preview unavailable."));
                    return;
                }

                renderPreview(data);
            } catch (_error) {
                if (currentRequest === requestId) {
                    renderEmpty(financeTranslate("Preview unavailable."));
                }
            }
        }

        function schedulePreview() {
            window.clearTimeout(timer);
            timer = window.setTimeout(updatePreview, 250);
        }

        form.addEventListener("input", schedulePreview);
        form.addEventListener("change", schedulePreview);
        refresh?.addEventListener("click", updatePreview);
        form.closest(".modal")?.addEventListener("show.bs.modal", updatePreview);
        form.closest(".modal")?.addEventListener("shown.bs.modal", updatePreview);
    });
}

setupRulePreviewForms();

function setupRuleTableActions(root = document) {
    const forms = Array.from(root.querySelectorAll("[data-rule-table-action]"));
    const status = document.querySelector("[data-rules-ajax-status]");
    const table = document.querySelector("[data-rules-table]");

    if (!forms.length) {
        return;
    }

    function showStatus(message, isError = false) {
        if (!status) {
            return;
        }

        status.textContent = message;
        status.classList.remove("d-none", "alert-info", "alert-danger");
        status.classList.add(isError ? "alert-danger" : "alert-info");
    }

    function ruleRow(ruleId) {
        return document.querySelector(`[data-rule-row="${CSS.escape(String(ruleId))}"]`);
    }

    function removeRuleRow(ruleId, options = {}) {
        ruleRow(ruleId)?.remove();
        document.getElementById(`edit-rule-${ruleId}`)?.remove();
        if (options.removeDeleteModal !== false) {
            document.getElementById(`delete-rule-${ruleId}`)?.remove();
        }
    }

    function handleApprove(data, form) {
        const selectedApproval = table?.dataset.selectedApproval || "";
        if (selectedApproval === "suggested") {
            removeRuleRow(data.rule_id);
            return;
        }

        const row = ruleRow(data.rule_id);
        const badge = row?.querySelector("[data-rule-approval-badge]");
        if (badge) {
            badge.textContent = financeTranslate(data.approval_label || "Approved");
            badge.className = `badge ${data.approval_badge_class || "text-bg-success"}`;
        }
        form.remove();
    }

    function hideOwningModal(form) {
        const modalElement = form.closest(".modal");
        if (!modalElement || !window.bootstrap?.Modal) {
            return;
        }

        bootstrap.Modal.getOrCreateInstance(modalElement).hide();
    }

    async function submitAction(form) {
        const buttons = Array.from(form.querySelectorAll("button"));
        buttons.forEach((button) => {
            button.disabled = true;
        });

        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: new FormData(form),
                headers: {
                    "X-Requested-With": "fetch",
                    "X-CSRF-Token": getCsrfToken(),
                },
            });
            const data = await response.json().catch(() => ({
                ok: false,
                message: "The rule action could not be completed.",
            }));

            if (!response.ok || data.ok === false) {
                showStatus(data.message || "The rule action could not be completed.", true);
                return;
            }

            if (data.action === "approve") {
                handleApprove(data, form);
            } else if (data.action === "delete") {
                const modalElement = form.closest(".modal");
                hideOwningModal(form);
                removeRuleRow(data.rule_id, { removeDeleteModal: false });
                if (modalElement) {
                    modalElement.addEventListener("hidden.bs.modal", () => modalElement.remove(), { once: true });
                    if (!window.bootstrap?.Modal) {
                        modalElement.remove();
                    }
                }
            }

            showStatus(data.message || "Rule updated.");
        } catch (_error) {
            showStatus("The rule action could not be completed.", true);
        } finally {
            if (document.body.contains(form)) {
                buttons.forEach((button) => {
                    button.disabled = false;
                });
            }
        }
    }

    forms.forEach((form) => {
        if (form.dataset.ruleTableActionReady === "true") {
            return;
        }

        form.dataset.ruleTableActionReady = "true";
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            submitAction(form);
        });
    });
}

setupRuleTableActions();
