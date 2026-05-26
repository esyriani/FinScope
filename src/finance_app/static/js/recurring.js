function setupRecurringActivityDetailModal() {
    const dataNode = document.getElementById("recurring-activity-data");
    const modalElement = document.getElementById("recurring-activity-modal");
    if (!dataNode || !modalElement || !window.bootstrap?.Modal) return;

    let recurringData = {};
    try {
        recurringData = JSON.parse(dataNode.textContent || "{}");
    } catch (_error) {
        recurringData = {};
    }

    const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
    const title = modalElement.querySelector("#recurring-activity-modal-title");
    const subtitle = modalElement.querySelector("[data-recurring-detail-subtitle]");
    const statusPill = modalElement.querySelector("[data-recurring-detail-status-pill]");
    const statusDetail = modalElement.querySelector("[data-recurring-detail-status-detail]");
    const userStatus = modalElement.querySelector("[data-recurring-detail-user-status]");
    const recommendation = modalElement.querySelector("[data-recurring-detail-recommendation]");
    const type = modalElement.querySelector("[data-recurring-detail-type]");
    const frequency = modalElement.querySelector("[data-recurring-detail-frequency]");
    const amount = modalElement.querySelector("[data-recurring-detail-amount]");
    const expectedDate = modalElement.querySelector("[data-recurring-detail-date]");
    const lastSeen = modalElement.querySelector("[data-recurring-detail-last-seen]");
    const observed = modalElement.querySelector("[data-recurring-detail-observed]");
    const confidence = modalElement.querySelector("[data-recurring-detail-confidence]");
    const explanation = modalElement.querySelector("[data-recurring-detail-explanation]");
    const match = modalElement.querySelector("[data-recurring-detail-match]");
    const occurrences = modalElement.querySelector("[data-recurring-detail-occurrences]");
    const amountChangePanel = modalElement.querySelector("[data-recurring-detail-amount-change]");
    const amountChangeTypical = modalElement.querySelector("[data-recurring-detail-change-typical]");
    const amountChangeActual = modalElement.querySelector("[data-recurring-detail-change-actual]");
    const amountChangeDifference = modalElement.querySelector("[data-recurring-detail-change-difference]");
    const actionStatus = modalElement.querySelector("[data-recurring-detail-action-status]");
    const confirmAction = modalElement.querySelector("[data-recurring-confirm-action]");
    const ignoreAction = modalElement.querySelector("[data-recurring-ignore-action]");
    const editAction = modalElement.querySelector("[data-recurring-edit-action]");
    const saveEditAction = modalElement.querySelector("[data-recurring-save-edit-action]");
    const editPanel = modalElement.querySelector("[data-recurring-edit-panel]");
    const editFrequency = modalElement.querySelector("[data-recurring-edit-frequency]");
    const editDate = modalElement.querySelector("[data-recurring-edit-date]");
    const editAmount = modalElement.querySelector("[data-recurring-edit-amount]");
    const editDateTolerance = modalElement.querySelector("[data-recurring-edit-date-tolerance]");
    const editAmountTolerance = modalElement.querySelector("[data-recurring-edit-amount-tolerance]");
    const editActive = modalElement.querySelector("[data-recurring-edit-active]");
    const moneyFormatter = new Intl.NumberFormat(window.financeLocale || "en-CA", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
    const dayDataNode = document.getElementById("recurring-calendar-day-data");
    const dayModalElement = document.getElementById("recurring-calendar-day-modal");
    let recurringDayData = [];
    try {
        recurringDayData = JSON.parse(dayDataNode?.textContent || "[]");
    } catch (_error) {
        recurringDayData = [];
    }
    const dayModal = dayModalElement && window.bootstrap?.Modal
        ? bootstrap.Modal.getOrCreateInstance(dayModalElement)
        : null;
    const dayModalTitle = dayModalElement?.querySelector("#recurring-calendar-day-modal-title");
    const dayModalSummary = dayModalElement?.querySelector("[data-recurring-calendar-day-summary]");
    const dayModalList = dayModalElement?.querySelector("[data-recurring-calendar-day-list]");
    // Display fallback only; current recurrence rows include the configured tolerance in matchDetails.
    const fallbackDateToleranceDays = 5;
    let activeRecurringId = "";
    const ignoredRecurringIds = new Set();

    function formatMoneyLocal(value) {
        return moneyFormatter.format(Number(value) || 0).replace(/,/g, " ") + " $";
    }

    function formatDateLocal(value) {
        if (!value) return "";
        const date = new Date(`${value}T00:00:00`);
        if (Number.isNaN(date.getTime())) return String(value);
        const day = String(date.getDate()).padStart(2, "0");
        const month = date.toLocaleString(window.financeLocale || "en-CA", { month: "short" });
        return `${day}-${month}-${date.getFullYear()}`;
    }

    function escapeHtmlLocal(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function statusLabel(value) {
        if (value === "occurred") return financeTranslate("Occurred");
        if (value === "amount_changed") return financeTranslate("Amount changed");
        if (value === "likely_occurred") return financeTranslate("Likely occurred");
        if (value === "matched") return financeTranslate("Likely occurred");
        if (value === "expected") return financeTranslate("Expected");
        if (value === "overdue") return financeTranslate("Overdue");
        if (value === "possibly_inactive") return financeTranslate("Possibly inactive");
        return String(value || "");
    }

    function recurringStatusLabel(item) {
        return item.statusLabel ? financeTranslate(item.statusLabel) : statusLabel(item.status);
    }

    function recurringStatusClass(value) {
        return String(value || "").replace(/[^a-z0-9_-]/gi, "");
    }

    function confidenceBadgeClass(value) {
        if (value === "High") return "text-bg-success";
        if (value === "Medium") return "text-bg-warning";
        return "text-bg-secondary";
    }

    function actionRecommendation(item) {
        if (item.userStatus === "confirmed" || item.userStatus === "edited") {
            return financeTranslate("No action needed unless the pattern changed.");
        }
        if (
            item.status === "overdue"
            || item.status === "amount_changed"
            || item.status === "possibly_inactive"
            || item.confidence === "Low"
        ) {
            return financeTranslate("Review this pattern before relying on it.");
        }
        if (item.status === "expected") {
            return financeTranslate("Wait for it to appear, or edit the expected date if the timing changed.");
        }
        return financeTranslate("Confirm recurring if this pattern is useful; ignore it if it is noise.");
    }

    function userStatusLabel(item) {
        if (item.userStatus === "confirmed") return financeTranslate("Confirmed by you");
        if (item.userStatus === "edited") return financeTranslate("Edited by you");
        if (item.userStatus === "ignored") return financeTranslate("Ignored");
        return financeTranslate("Not reviewed yet");
    }

    function rowUserStatusLabel(item) {
        if (item.userStatus === "confirmed") return financeTranslate("Confirmed by you");
        if (item.userStatus === "edited") return financeTranslate("Edited by you");
        return "";
    }

    function signedNumber(value, suffix) {
        const numberValue = Number(value);
        if (!Number.isFinite(numberValue)) return "";
        const prefix = numberValue > 0 ? "+" : "";
        return `${prefix}${numberValue}${suffix}`;
    }

    function detailValue(details, snakeName, camelName) {
        return details?.[snakeName] ?? details?.[camelName];
    }

    function matchText(item) {
        const details = item.matchDetails || {};
        if (item.status === "possibly_inactive") {
            return detailValue(details, "inactive_reason", "inactiveReason")
                || financeTranslate("This pattern has missed multiple expected cycles.");
        }
        const matchedDate = detailValue(details, "matched_date", "matchedDate");
        const dateDifferenceDays = detailValue(details, "date_difference_days", "dateDifferenceDays");
        const amountDifference = detailValue(details, "amount_difference", "amountDifference");
        const dateToleranceDays = detailValue(details, "date_tolerance_days", "dateToleranceDays") || fallbackDateToleranceDays;
        const amountTolerance = detailValue(details, "amount_tolerance", "amountTolerance");
        if (!matchedDate) {
            return financeTranslate(
                "No current-month merchant and direction match was found. Expected date uses a +/-{days} day tolerance.",
                { days: dateToleranceDays }
            );
        }

        return [
            financeTranslate("Best current-month match: {date}.", { date: formatDateLocal(matchedDate) }),
            financeTranslate("Date difference: {difference}.", { difference: signedNumber(dateDifferenceDays, ` ${financeTranslate("days")}`) }),
            financeTranslate("Amount difference: {difference}.", { difference: formatMoneyLocal(amountDifference) }),
            financeTranslate(
                "Tolerances: +/-{days} days and +/-{amount}.",
                {
                    days: dateToleranceDays,
                    amount: formatMoneyLocal(amountTolerance)
                }
            )
        ].join(" ");
    }

    function showActionStatus(message, tone = "info") {
        if (!actionStatus) return;
        actionStatus.textContent = message;
        actionStatus.classList.remove("alert-info", "alert-success", "alert-danger");
        actionStatus.classList.add(`alert-${tone}`);
        actionStatus.classList.remove("d-none");
    }

    function hideActionStatus() {
        if (!actionStatus) return;
        actionStatus.textContent = "";
        actionStatus.classList.add("d-none");
    }

    function setEditPanelVisible(visible) {
        if (editPanel) editPanel.classList.toggle("d-none", !visible);
    }

    function setAmountChangeDetails(item) {
        const change = item.amountChange;
        if (!amountChangePanel) return;
        amountChangePanel.classList.toggle("d-none", !change);
        if (!change) return;

        const percentText = Number.isFinite(Number(change.percent))
            ? ` (${signedNumber(change.percent, "%")})`
            : "";
        if (amountChangeTypical) amountChangeTypical.textContent = formatMoneyLocal(change.typical_amount);
        if (amountChangeActual) amountChangeActual.textContent = formatMoneyLocal(change.actual_amount);
        if (amountChangeDifference) {
            amountChangeDifference.textContent = `${formatMoneyLocal(change.difference)}${percentText}`;
            amountChangeDifference.classList.toggle("text-danger", Number(change.difference) >= 0);
            amountChangeDifference.classList.toggle("text-success", Number(change.difference) < 0);
        }
    }

    function populateEditForm(item) {
        const details = item.matchDetails || {};
        if (editFrequency) editFrequency.value = item.frequency || "Irregular recurring";
        if (editDate) editDate.value = item.date || "";
        if (editAmount) editAmount.value = Number(item.amount || 0).toFixed(2);
        if (editDateTolerance) editDateTolerance.value = details.date_tolerance_days || fallbackDateToleranceDays;
        if (editAmountTolerance) editAmountTolerance.value = Number(details.amount_tolerance || 0).toFixed(2);
        if (editActive) editActive.value = item.active === 0 ? "inactive" : "active";
    }

    function visibleRecurringItems(items) {
        return (items || []).filter((item) => !ignoredRecurringIds.has(item.id));
    }

    function recurringElements(item) {
        return Array.from(document.querySelectorAll("[data-recurring-id]"))
            .filter((element) => element.dataset.recurringId === item.id);
    }

    function syncVisibleRecurringState(item) {
        recurringElements(item).forEach((element) => {
            element.dataset.recurringUserStatus = item.userStatus || "detected";
            element.dataset.recurringActive = String(item.active ?? 1);
            const rowState = element.querySelector("[data-recurring-row-state]");
            if (rowState) {
                const label = rowUserStatusLabel(item);
                rowState.textContent = label;
                rowState.classList.toggle("d-none", !label);
            }
        });
    }

    function syncModalDecisionState(item) {
        if (userStatus) userStatus.textContent = userStatusLabel(item);
        if (recommendation) recommendation.textContent = actionRecommendation(item);
        if (confirmAction) {
            const alreadyConfirmed = item.userStatus === "confirmed" && item.active !== 0;
            confirmAction.disabled = alreadyConfirmed;
            confirmAction.setAttribute("aria-disabled", alreadyConfirmed ? "true" : "false");
        }
    }

    function applyIgnoredRecurringState() {
        document.querySelectorAll("[data-recurring-id]").forEach((element) => {
            const ignored = ignoredRecurringIds.has(element.dataset.recurringId);
            element.dataset.recurringIgnored = ignored ? "true" : "false";
            element.hidden = ignored;
        });
    }

    function patternPayload(item) {
        return {
            patternKey: item.patternKey,
            merchantId: item.merchantId,
            matchType: item.matchType,
            merchant: item.merchant,
            type: item.type
        };
    }

    async function postRecurringPattern(url, payload) {
        const response = await fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": getCsrfToken()
            },
            body: JSON.stringify(payload)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
            throw new Error(data.message || financeTranslate("Could not save recurring pattern."));
        }
        return data;
    }

    function openRecurringDetail(id) {
        const item = recurringData[id];
        if (!item) return;

        activeRecurringId = id;
        title.textContent = item.merchant || financeTranslate("Recurring activity");
        subtitle.textContent = item.category || "";
        if (statusPill) {
            statusPill.textContent = recurringStatusLabel(item);
            statusPill.className = `recurring-status-pill recurring-status-${recurringStatusClass(item.status)}`;
        }
        if (statusDetail) statusDetail.textContent = item.statusDetail || "";
        syncModalDecisionState(item);
        syncVisibleRecurringState(item);
        type.textContent = item.type === "income" ? financeTranslate("Income") : financeTranslate("Spending");
        frequency.textContent = financeTranslate(item.frequency || "Irregular recurring");
        amount.textContent = formatMoneyLocal(item.amount);
        expectedDate.textContent = formatDateLocal(item.date);
        lastSeen.textContent = formatDateLocal(item.lastSeen);
        observed.textContent = financeTranslate(
            (item.observedMonths || 0) === 1 ? "{count} distinct month" : "{count} distinct months",
            { count: item.observedMonths || 0 }
        );
        confidence.innerHTML = `<span class="badge ${confidenceBadgeClass(item.confidence)}">${escapeHtmlLocal(financeTranslate(item.confidence || "Low"))}</span>`;
        explanation.textContent = financeTranslate(
            "Detected because this merchant appeared in {months} distinct months with a typical amount of {amount}.",
            { months: item.observedMonths || 0, amount: formatMoneyLocal(item.amount) }
        );
        match.textContent = matchText(item);
        setAmountChangeDetails(item);
        hideActionStatus();
        setEditPanelVisible(false);
        populateEditForm(item);
        if (item.userStatus === "confirmed") {
            showActionStatus(financeTranslate("Confirmed recurring."));
        } else if (item.userStatus === "edited") {
            showActionStatus(financeTranslate("This recurring pattern has saved user edits."));
        }

        const rows = item.occurrences || [];
        occurrences.innerHTML = rows.length ? rows.map((occurrence) => `
            <tr>
                <td><a class="text-reset text-decoration-none" href="${escapeHtmlLocal(occurrence.url)}">${escapeHtmlLocal(formatDateLocal(occurrence.date))}</a></td>
                <td>${escapeHtmlLocal(occurrence.description || "")}</td>
                <td>${escapeHtmlLocal(occurrence.category)}</td>
                <td>${escapeHtmlLocal(occurrence.account_name || financeTranslate("Personal"))}</td>
                <td class="text-end ${occurrence.type === "income" ? "text-success" : "text-danger"}">${formatMoneyLocal(occurrence.amount)}</td>
            </tr>
        `).join("") : `<tr><td colspan="5" class="text-muted">${escapeHtmlLocal(financeTranslate("No recent occurrences available."))}</td></tr>`;

        modal.show();
    }

    function wireDetailTrigger(element) {
        const isTableRow = element.matches("tr[data-recurring-id]");
        if (isTableRow) {
            element.addEventListener("dblclick", () => openRecurringDetail(element.dataset.recurringId));
        } else {
            element.addEventListener("click", () => openRecurringDetail(element.dataset.recurringId));
        }
        element.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                openRecurringDetail(element.dataset.recurringId);
            }
        });
    }

    function openRecurringDay(date) {
        if (!dayModal || !dayModalList) return;

        const day = recurringDayData.find((item) => item.date === date);
        const items = visibleRecurringItems(day?.all_recurring_items);
        if (!day || !items.length) return;

        if (dayModalTitle) dayModalTitle.textContent = financeTranslate("Recurring items - {date}", { date: formatDateLocal(date) });
        if (dayModalSummary) {
            dayModalSummary.textContent = financeTranslate(
                items.length === 1 ? "{count} recurring item" : "{count} recurring items",
                { count: items.length }
            );
            const attentionCount = items.filter((item) => item.needs_attention).length;
            if (attentionCount) {
                dayModalSummary.textContent += ` - ${financeTranslate("Needs attention: {count}", { count: attentionCount })}`;
            }
        }

        dayModalList.innerHTML = items.map((item) => `
            <button class="recurring-calendar-day-list-item recurring-status-${recurringStatusClass(item.status)}" type="button" data-recurring-id="${escapeHtmlLocal(item.id)}" data-recurring-user-status="${escapeHtmlLocal(item.user_status || "detected")}" data-recurring-active="${escapeHtmlLocal(item.active ?? 1)}">
                <span class="recurring-calendar-day-list-status">${escapeHtmlLocal(financeTranslate(item.status_label))}</span>
                <span class="recurring-calendar-day-list-main">
                    <strong>${escapeHtmlLocal(item.merchant)}</strong>
                    <small>${escapeHtmlLocal([item.category, financeTranslate(item.frequency), item.status_detail].filter(Boolean).join(" - "))}</small>
                </span>
                <em class="${escapeHtmlLocal(item.amount_class)}">${escapeHtmlLocal(item.amount_label)}</em>
            </button>
        `).join("");

        dayModalList.querySelectorAll("[data-recurring-id]").forEach((itemButton) => {
            itemButton.addEventListener("click", () => {
                dayModal.hide();
                openRecurringDetail(itemButton.dataset.recurringId);
            });
        });

        dayModal.show();
    }

    confirmAction?.addEventListener("click", async () => {
        if (!activeRecurringId) return;
        const item = recurringData[activeRecurringId];
        if (!item) return;
        try {
            await postRecurringPattern("/recurring/patterns/confirm", patternPayload(item));
            item.userStatus = "confirmed";
            item.active = 1;
            syncModalDecisionState(item);
            syncVisibleRecurringState(item);
            showActionStatus(financeTranslate("Confirmed recurring."), "success");
        } catch (error) {
            showActionStatus(error.message, "danger");
        }
    });

    ignoreAction?.addEventListener("click", async () => {
        if (!activeRecurringId) return;
        const item = recurringData[activeRecurringId];
        if (!item) return;
        try {
            await postRecurringPattern("/recurring/patterns/ignore", patternPayload(item));
            item.userStatus = "ignored";
            item.active = 0;
            ignoredRecurringIds.add(activeRecurringId);
            syncModalDecisionState(item);
            syncVisibleRecurringState(item);
            applyIgnoredRecurringState();
            modal.hide();
        } catch (error) {
            showActionStatus(error.message, "danger");
        }
    });

    editAction?.addEventListener("click", () => {
        const item = recurringData[activeRecurringId];
        if (item) populateEditForm(item);
        setEditPanelVisible(true);
        hideActionStatus();
    });

    saveEditAction?.addEventListener("click", async () => {
        if (!activeRecurringId) return;
        const item = recurringData[activeRecurringId];
        if (!item) return;
        const payload = {
            ...patternPayload(item),
            frequency: editFrequency?.value || item.frequency,
            expectedDate: editDate?.value || item.date,
            typicalAmount: editAmount?.value,
            dateToleranceDays: editDateTolerance?.value,
            amountTolerance: editAmountTolerance?.value,
            active: editActive?.value || "active"
        };
        try {
            const result = await postRecurringPattern("/recurring/patterns/edit", payload);
            item.userStatus = "edited";
            item.active = result.active;
            item.frequency = payload.frequency;
            item.amount = Number(payload.typicalAmount || item.amount);
            item.date = payload.expectedDate || item.date;
            item.matchDetails = {
                ...(item.matchDetails || {}),
                date_tolerance_days: Number(payload.dateToleranceDays || fallbackDateToleranceDays),
                amount_tolerance: Number(payload.amountTolerance || 0)
            };
            setEditPanelVisible(false);
            frequency.textContent = financeTranslate(item.frequency || "Irregular recurring");
            amount.textContent = formatMoneyLocal(item.amount);
            expectedDate.textContent = formatDateLocal(item.date);
            syncModalDecisionState(item);
            syncVisibleRecurringState(item);
            showActionStatus(financeTranslate("Recurring pattern changes saved."), "success");
            if (item.active === 0) {
                ignoredRecurringIds.add(activeRecurringId);
                applyIgnoredRecurringState();
                modal.hide();
            }
        } catch (error) {
            showActionStatus(error.message, "danger");
        }
    });

    document.querySelectorAll("[data-recurring-id]").forEach((element) => {
        if (element.closest("[data-recurring-calendar-day-list]")) return;
        wireDetailTrigger(element);
    });

    document.querySelectorAll("[data-recurring-day-open], [data-recurring-day-more]").forEach((button) => {
        const dayValue = button.dataset.recurringDayOpen || button.dataset.recurringDayMore;
        button.addEventListener("click", () => openRecurringDay(dayValue));
        button.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                openRecurringDay(dayValue);
            }
        });
    });

    document.querySelectorAll("[data-recurring-calendar-day]").forEach((day) => {
        day.addEventListener("click", (event) => {
            if (event.target.closest("a, button")) return;
            openRecurringDay(day.dataset.recurringCalendarDay);
        });
    });

    applyIgnoredRecurringState();
}

function setupRecurringAjaxNavigation() {
    if (document.body.dataset.recurringAjaxReady === "true") return;
    document.body.dataset.recurringAjaxReady = "true";

    const dynamicSelector = "[data-recurring-dynamic]";

    function recurringUrl(value) {
        const url = new URL(value, window.location.href);
        return url.origin === window.location.origin && url.pathname === "/recurring" ? url : null;
    }

    function setDynamicBusy(busy) {
        const dynamic = document.querySelector(dynamicSelector);
        if (!dynamic) return;
        dynamic.setAttribute("aria-busy", busy ? "true" : "false");
        dynamic.classList.toggle("recurring-dynamic-loading", busy);
    }

    function closeOpenRecurringModals() {
        document.querySelectorAll(".modal.show").forEach((modalElement) => {
            window.bootstrap?.Modal.getInstance(modalElement)?.hide();
        });
    }

    function initializeRecurringDynamic(dynamic) {
        window.setupFlatpickrInputs?.();
        window.setupTableRowInteractions?.(dynamic);
        window.setupSortableTables?.(dynamic);
        window.setupPaginatedTables?.(dynamic);
        window.setupTableExports?.();
        setupRecurringActivityDetailModal();
    }

    function setHiddenField(form, name, value) {
        let field = form.querySelector(`input[type="hidden"][name="${name}"]`);
        if (!field) {
            field = document.createElement("input");
            field.type = "hidden";
            field.name = name;
            form.prepend(field);
        }
        field.value = value;
    }

    function syncRecurringFilterForm(url) {
        const form = document.querySelector(".recurring-control-row");
        if (!form) return;

        setHiddenField(form, "view", url.searchParams.get("view") || "list");
        setHiddenField(form, "month", url.searchParams.get("month") || "");
        form.querySelectorAll('input[type="hidden"][name="statuses"]').forEach((field) => field.remove());
        url.searchParams.getAll("statuses").forEach((status) => {
            const field = document.createElement("input");
            field.type = "hidden";
            field.name = "statuses";
            field.value = status;
            form.prepend(field);
        });
    }

    async function replaceRecurringDynamic(url, pushState = true) {
        const currentDynamic = document.querySelector(dynamicSelector);
        if (!currentDynamic) {
            window.location.href = url.toString();
            return;
        }

        setDynamicBusy(true);
        try {
            const response = await fetch(url.toString(), {
                headers: { "X-Requested-With": "XMLHttpRequest" }
            });
            if (!response.ok) throw new Error("Recurring refresh failed.");

            const documentText = await response.text();
            const nextDocument = new DOMParser().parseFromString(documentText, "text/html");
            const nextDynamic = nextDocument.querySelector(dynamicSelector);
            if (!nextDynamic) throw new Error("Recurring refresh returned no content.");

            closeOpenRecurringModals();
            currentDynamic.replaceWith(document.importNode(nextDynamic, true));
            if (pushState) {
                window.history.pushState({ recurringAjax: true }, "", url.toString());
            }
            syncRecurringFilterForm(url);
            initializeRecurringDynamic(document.querySelector(dynamicSelector));
        } catch (_error) {
            window.location.href = url.toString();
        } finally {
            setDynamicBusy(false);
        }
    }

    function formUrl(form) {
        const url = new URL(form.getAttribute("action") || window.location.href, window.location.href);
        url.search = new URLSearchParams(new FormData(form)).toString();
        return url;
    }

    document.addEventListener("click", (event) => {
        const link = event.target.closest("[data-recurring-ajax-link]");
        if (!link) return;

        const url = recurringUrl(link.href);
        if (!url) return;

        event.preventDefault();
        replaceRecurringDynamic(url);
    });

    document.addEventListener("submit", (event) => {
        const form = event.target.closest("[data-recurring-ajax-form]");
        if (!form) return;

        const url = recurringUrl(formUrl(form));
        if (!url) return;

        event.preventDefault();
        replaceRecurringDynamic(url);
    });

    window.addEventListener("popstate", () => {
        const url = recurringUrl(window.location.href);
        if (url) replaceRecurringDynamic(url, false);
    });
}

setupRecurringActivityDetailModal();
setupRecurringAjaxNavigation();
