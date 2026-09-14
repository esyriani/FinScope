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

    function moneyNumber(value) {
        if (value === null || value === undefined) {
            return null;
        }
        if (typeof value !== "number" && typeof value !== "string") {
            return null;
        }
        if (typeof value === "string" && value.trim() === "") {
            return null;
        }

        const numberValue = Number(value);
        return Number.isFinite(numberValue) ? numberValue : null;
    }

    function money(value) {
        if (window.financeFormatMoney) {
            return window.financeFormatMoney(value);
        }

        const numberValue = moneyNumber(value);
        return numberValue === null ? "" : numberValue.toFixed(2);
    }

    function parseAmount(value) {
        const parsed = Number.parseFloat(String(value || "").replace(",", "."));
        return Number.isFinite(parsed) ? parsed : 0;
    }

    function parseJsonScript(scope, selector) {
        const script = scope.querySelector(selector) || document.querySelector(selector);
        if (!script) {
            return [];
        }

        try {
            const value = JSON.parse(script.textContent || "[]");
            return Array.isArray(value) ? value : [];
        } catch (_error) {
            return [];
        }
    }

    function findPayloadById(items, id) {
        return items.find((item) => String(item.id) === String(id));
    }

    function modalDataScope(modal) {
        return modal.closest("[data-ajax-refresh-target]") || document;
    }

    function dataValueFromSource(source, selector, key) {
        const dataSource = source?.closest(selector);
        return dataSource?.dataset[key] || "";
    }

    function setText(root, selector, value) {
        const element = root.querySelector(selector);
        if (element) {
            element.textContent = value == null ? "" : String(value);
        }
    }

    function setValue(root, selector, value) {
        const element = root.querySelector(selector);
        if (element) {
            element.value = value == null ? "" : String(value);
        }
    }

    function setHidden(root, selector, hidden) {
        const element = root.querySelector(selector);
        if (element) {
            element.hidden = hidden;
        }
    }

    function textElement(tagName, className, text) {
        const element = document.createElement(tagName);
        if (className) {
            element.className = className;
        }
        element.textContent = text == null ? "" : String(text);
        return element;
    }

    function appendMoneyFact(container, label, value, amountClass) {
        const wrapper = document.createElement("div");
        wrapper.appendChild(textElement("span", "", label));
        wrapper.appendChild(textElement("strong", amountClass, value));
        container.appendChild(wrapper);
    }

    function candidateShell() {
        const candidate = document.createElement("div");
        candidate.className = "reimbursement-candidate";
        candidate.dataset.matchCandidate = "true";
        return candidate;
    }

    function appendCandidateChoice(candidate, checkboxId, checkboxName, checkboxValue, title, description) {
        const choice = document.createElement("div");
        choice.className = "form-check reimbursement-candidate-choice";

        const checkbox = document.createElement("input");
        checkbox.className = "form-check-input";
        checkbox.type = "checkbox";
        checkbox.name = checkboxName;
        checkbox.value = String(checkboxValue);
        checkbox.id = checkboxId;
        checkbox.dataset.matchCheckbox = "true";

        const label = document.createElement("label");
        label.className = "form-check-label";
        label.setAttribute("for", checkboxId);
        label.appendChild(textElement("span", "reimbursement-candidate-title", title));
        label.appendChild(textElement("span", "reimbursement-candidate-description", description));

        choice.append(checkbox, label);
        candidate.appendChild(choice);
    }

    function appendMatchAmountInput(candidate, inputId, inputName, defaultAmount, maxAmount, amountLabel) {
        const wrapper = document.createElement("div");
        wrapper.className = "reimbursement-match-amount";

        const label = document.createElement("label");
        label.className = "form-label";
        label.setAttribute("for", inputId);
        label.textContent = amountLabel;

        const input = document.createElement("input");
        input.id = inputId;
        input.className = "form-control form-control-sm text-end";
        input.type = "number";
        input.name = inputName;
        input.min = "0.01";
        input.max = String(maxAmount);
        input.step = "0.01";
        input.value = String(defaultAmount);
        input.dataset.matchAmount = "true";

        wrapper.append(label, input);
        candidate.querySelector(".reimbursement-candidate-money")?.appendChild(wrapper);
    }

    function buildExpenseCandidate(candidate, reimbursementId, labels) {
        const wrapper = candidateShell();
        const checkboxId = `match-${reimbursementId}-expense-${candidate.id}`;
        appendCandidateChoice(
            wrapper,
            checkboxId,
            "expense_transaction_ids",
            candidate.id,
            `${candidate.date_label} - ${candidate.category}`,
            candidate.description
        );

        const moneyColumn = document.createElement("div");
        moneyColumn.className = "reimbursement-candidate-money";
        appendMoneyFact(moneyColumn, labels.paid, candidate.amount_label, "amount-paid");
        appendMoneyFact(moneyColumn, labels.reimbursed, candidate.allocated_label, "amount-reimbursed");
        appendMoneyFact(moneyColumn, labels.stillToReimburse, candidate.pending_remaining_label, "amount-owed");
        wrapper.appendChild(moneyColumn);

        appendMatchAmountInput(
            wrapper,
            `${checkboxId}-amount`,
            `amount_${candidate.id}`,
            candidate.default_amount,
            candidate.max_amount,
            labels.matchAmount
        );
        return wrapper;
    }

    function buildReimbursementCandidate(candidate, expenseId, labels) {
        const wrapper = candidateShell();
        const checkboxId = `expense-${expenseId}-reimbursement-${candidate.id}`;
        appendCandidateChoice(
            wrapper,
            checkboxId,
            "reimbursement_transaction_ids",
            candidate.id,
            `${candidate.date_label} - ${labels.reimbursement}`,
            candidate.description
        );

        const moneyColumn = document.createElement("div");
        moneyColumn.className = "reimbursement-candidate-money";
        appendMoneyFact(moneyColumn, labels.receivedAmount, candidate.amount_label, "amount-received");
        appendMoneyFact(moneyColumn, labels.alreadyMatched, candidate.allocated_label, "amount-matched");
        appendMoneyFact(moneyColumn, labels.unmatched, candidate.remaining_label, "amount-unmatched");
        wrapper.appendChild(moneyColumn);

        appendMatchAmountInput(
            wrapper,
            `${checkboxId}-amount`,
            `amount_${candidate.id}`,
            candidate.default_amount,
            candidate.max_amount,
            labels.matchAmount
        );
        return wrapper;
    }

    function resetMatchForm(form) {
        if (!form) {
            return;
        }
        delete form.dataset.reimbursementMatchReady;
        setupMatchForm(form);
    }

    function setupMatchForm(form) {
        if (form.dataset.reimbursementMatchReady === "true") {
            return;
        }
        form.dataset.reimbursementMatchReady = "true";

        const matchRemaining = parseAmount(form.dataset.matchRemaining || form.dataset.reimbursementRemaining);
        const total = form.querySelector("[data-match-total]");
        const remaining = form.querySelector("[data-match-remaining-label]");
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

            const remainingAfterMatch = matchRemaining - selectedTotal;
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

    function populateReimbursementMatchModal(modal, source) {
        const reimbursementId = dataValueFromSource(source, "[data-reimbursement-match-id]", "reimbursementMatchId");
        const item = findPayloadById(
            parseJsonScript(modalDataScope(modal), "[data-reimbursement-match-items]"),
            reimbursementId
        );
        if (!item) {
            return;
        }

        const form = modal.querySelector("[data-reimbursement-match-form]");
        const candidates = item.candidates || [];
        const hasCandidates = candidates.length > 0;
        const candidateList = modal.querySelector("[data-reimbursement-match-candidates]");
        const note = modal.querySelector("[data-reimbursement-match-note]");
        const submit = modal.querySelector("[data-match-submit]");

        if (form) {
            form.dataset.matchRemaining = item.remaining;
            form.dataset.reimbursementRemaining = item.remaining;
        }
        setValue(modal, "[data-reimbursement-match-id]", item.id);
        setText(modal, "[data-reimbursement-match-subtitle]", `${item.date_label} - ${item.description}`);
        setText(modal, "[data-reimbursement-match-amount-label]", item.amount_label);
        setText(modal, "[data-reimbursement-match-allocated-label]", item.allocated_label);
        setText(modal, "[data-reimbursement-match-remaining-label]", item.remaining_label);
        setText(modal, "[data-reimbursement-match-candidate-count]", item.candidate_count_label);
        setText(modal, "[data-match-remaining-label]", item.remaining_label);

        candidateList?.replaceChildren(
            ...candidates.map((candidate) =>
                buildExpenseCandidate(candidate, item.id, {
                    paid: modal.dataset.paidLabel,
                    reimbursed: modal.dataset.reimbursedLabel,
                    stillToReimburse: modal.dataset.stillToReimburseLabel,
                    matchAmount: modal.dataset.matchAmountLabel,
                })
            )
        );

        if (note) {
            note.textContent = item.hidden_candidate_message || "";
            note.hidden = !item.hidden_candidate_message;
        }
        setHidden(modal, "[data-reimbursement-match-empty]", hasCandidates);
        setHidden(modal, "[data-reimbursement-match-review]", !hasCandidates);
        if (submit) {
            submit.hidden = !hasCandidates;
        }
        resetMatchForm(form);
    }

    function populateTagPills(container, tags, emptyLabel) {
        if (!container) {
            return;
        }
        if (!tags?.length) {
            container.replaceChildren(textElement("span", "text-muted", emptyLabel));
            return;
        }

        const list = document.createElement("span");
        list.className = "tag-pill-list";
        tags.forEach((tag) => {
            const pill = textElement("span", "tag-pill", tag.name);
            pill.style.setProperty("--tag-color", tag.color || "#64748b");
            list.appendChild(pill);
        });
        container.replaceChildren(list);
    }

    function populateExpenseMatches(modal, matches) {
        const body = modal.querySelector("[data-reimbursement-expense-matches]");
        const table = modal.querySelector("[data-reimbursement-expense-match-table]");
        const empty = modal.querySelector("[data-reimbursement-expense-matches-empty]");
        const rows = (matches || []).map((match) => {
            const row = document.createElement("tr");
            const detailCell = document.createElement("td");
            detailCell.appendChild(textElement("div", "", match.date_label));
            detailCell.appendChild(textElement("div", "small text-muted", match.description));
            row.appendChild(detailCell);
            row.appendChild(textElement("td", "text-end amount-matched", match.amount_label));
            return row;
        });

        body?.replaceChildren(...rows);
        if (table) {
            table.hidden = rows.length === 0;
        }
        if (empty) {
            empty.hidden = rows.length > 0;
        }
    }

    function setExpenseActionForms(modal, source, item) {
        const actionSource = source?.closest("[data-reimbursement-expense-id]");
        const tagForm = modal.querySelector("[data-reimbursement-expense-tag-form]");
        const tagValue = modal.querySelector("[data-reimbursement-expense-tag-value]");
        const tagButton = modal.querySelector("[data-reimbursement-expense-tag-button]");
        const stateForm = modal.querySelector("[data-reimbursement-expense-state-form]");
        const stateButton = modal.querySelector("[data-reimbursement-expense-state-button]");
        const stateIcon = modal.querySelector("[data-reimbursement-expense-state-icon]");

        if (tagForm) {
            tagForm.action = actionSource?.dataset.reimbursementExpenseTagActionUrl || "";
        }
        if (tagValue) {
            tagValue.value = item.has_reimbursable_tag ? "0" : "1";
        }
        if (tagButton) {
            tagButton.className = item.has_reimbursable_tag ? "btn btn-outline-danger" : "btn btn-outline-secondary";
            setText(
                modal,
                "[data-reimbursement-expense-tag-action]",
                item.has_reimbursable_tag
                    ? modal.dataset.removeReimbursableTagLabel
                    : modal.dataset.setReimbursableTagLabel
            );
        }

        if (stateForm) {
            stateForm.action = item.is_complete
                ? actionSource?.dataset.reimbursementExpenseResumeActionUrl || ""
                : actionSource?.dataset.reimbursementExpenseCompleteActionUrl || "";
        }
        if (stateButton) {
            setText(
                modal,
                "[data-reimbursement-expense-state-action]",
                item.is_complete ? modal.dataset.resumeTrackingLabel : modal.dataset.markCompleteLabel
            );
        }
        if (stateIcon) {
            stateIcon.className = item.is_complete ? "bi bi-arrow-counterclockwise" : "bi bi-check2-circle";
        }
    }

    function populateReimbursementExpenseModal(modal, source) {
        const expenseId = dataValueFromSource(source, "[data-reimbursement-expense-id]", "reimbursementExpenseId");
        const item = findPayloadById(
            parseJsonScript(modalDataScope(modal), "[data-reimbursement-expense-items]"),
            expenseId
        );
        if (!item) {
            return;
        }

        const candidateForm = modal.querySelector("[data-reimbursement-expense-match-form]");
        const candidates = item.reimbursement_candidates || [];
        const hasCandidates = candidates.length > 0;
        const candidateList = modal.querySelector("[data-reimbursement-expense-candidates]");
        const note = modal.querySelector("[data-reimbursement-expense-note]");

        setText(modal, "[data-reimbursement-expense-subtitle]", `${item.date_label} - ${item.description}`);
        setText(modal, "[data-reimbursement-expense-description]", item.description);
        setText(modal, "[data-reimbursement-expense-date]", item.date_label);
        setText(modal, "[data-reimbursement-expense-category]", item.category);
        populateTagPills(
            modal.querySelector("[data-reimbursement-expense-tags]"),
            item.tags || [],
            modal.dataset.noTagsLabel
        );
        setText(modal, "[data-reimbursement-expense-account]", item.account_name);
        setText(modal, "[data-reimbursement-expense-amount]", item.amount_label);
        setText(modal, "[data-reimbursement-expense-tag-state]", item.has_reimbursable_tag_label);
        setText(modal, "[data-reimbursement-expense-paid-label]", item.amount_label);
        setText(modal, "[data-reimbursement-expense-allocated-label]", item.allocated_label);
        setText(modal, "[data-reimbursement-expense-remaining-label]", item.pending_remaining_label);
        setText(modal, "[data-reimbursement-expense-candidate-count]", item.reimbursement_candidate_count_label);
        setText(modal, "[data-reimbursement-expense-match-count]", item.matched_reimbursement_count_label);
        setText(modal, "[data-match-remaining-label]", item.pending_remaining_label);

        const status = modal.querySelector("[data-reimbursement-expense-status]");
        if (status) {
            status.className = `badge ${item.status_class || ""}`.trim();
            status.textContent = item.status_label || "";
        }

        if (candidateForm) {
            candidateForm.dataset.matchRemaining = item.pending_remaining;
        }
        setValue(modal, "[data-reimbursement-expense-id]", item.id);
        candidateList?.replaceChildren(
            ...candidates.map((candidate) =>
                buildReimbursementCandidate(candidate, item.id, {
                    reimbursement: modal.dataset.reimbursementLabel,
                    receivedAmount: modal.dataset.receivedAmountLabel,
                    alreadyMatched: modal.dataset.alreadyMatchedLabel,
                    unmatched: modal.dataset.unmatchedLabel,
                    matchAmount: modal.dataset.matchAmountLabel,
                })
            )
        );

        if (note) {
            note.textContent = item.hidden_reimbursement_candidate_message || "";
            note.hidden = !item.hidden_reimbursement_candidate_message;
        }
        if (candidateForm) {
            candidateForm.hidden = !hasCandidates;
        }
        setHidden(modal, "[data-reimbursement-expense-empty]", hasCandidates);
        populateExpenseMatches(modal, item.matched_reimbursements || []);
        setExpenseActionForms(modal, source, item);
        resetMatchForm(candidateForm);
    }

    function setupReimbursementModals(root = document) {
        root.querySelectorAll("[data-reimbursement-match-modal]").forEach((modal) => {
            if (modal.dataset.reimbursementMatchModalReady === "true") {
                return;
            }

            modal.dataset.reimbursementMatchModalReady = "true";
            modal.addEventListener("show.bs.modal", (event) => {
                populateReimbursementMatchModal(modal, event.relatedTarget || document.activeElement);
            });
        });

        root.querySelectorAll("[data-reimbursement-expense-modal]").forEach((modal) => {
            if (modal.dataset.reimbursementExpenseModalReady === "true") {
                return;
            }

            modal.dataset.reimbursementExpenseModalReady = "true";
            modal.addEventListener("show.bs.modal", (event) => {
                populateReimbursementExpenseModal(modal, event.relatedTarget || document.activeElement);
            });
        });
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
    window.financeApp.registerInitializer("reimbursements.modals", setupReimbursementModals);
    window.financeApp.registerInitializer("reimbursements.tabs", setupReimbursementTabPersistence);
    setupReimbursementMatches();
    setupReimbursementModals();
    setupReimbursementTabPersistence();
})();
