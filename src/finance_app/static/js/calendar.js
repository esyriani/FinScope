function setupCalendarDayModal(root = document) {
    const dataNode = document.getElementById("calendar-day-data");
    const modalElement = document.getElementById("calendar-day-modal");
    if (!dataNode || !modalElement || !window.bootstrap?.Modal) return;

    let calendarData = {};
    try {
        calendarData = JSON.parse(dataNode.textContent || "{}");
    } catch (_error) {
        calendarData = {};
    }

    const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
    const title = modalElement.querySelector("#calendar-day-modal-title");
    const summary = modalElement.querySelector("[data-calendar-modal-summary]");
    const empty = modalElement.querySelector("[data-calendar-modal-empty]");
    const table = modalElement.querySelector("[data-calendar-modal-table]");
    const transactionBody = modalElement.querySelector("[data-calendar-modal-transactions]");
    const link = modalElement.querySelector("[data-calendar-modal-link]");
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

    function formatMoney(value) {
        if (window.financeFormatMoney) {
            return window.financeFormatMoney(value);
        }

        const numberValue = moneyNumber(value);
        return numberValue === null ? "" : numberValue.toFixed(2);
    }

    function transactionRow(item) {
        const row = document.createElement("tr");
        const descriptionCell = document.createElement("td");
        const linkNode = document.createElement("a");
        linkNode.className = "text-reset text-decoration-none";
        linkNode.href = item.url || "#";
        linkNode.textContent = item.description || "";
        descriptionCell.appendChild(linkNode);

        const categoryCell = document.createElement("td");
        categoryCell.textContent = item.category || "";
        const accountCell = document.createElement("td");
        accountCell.textContent = item.accountName || "";
        const amountCell = document.createElement("td");
        amountCell.className = `text-end ${item.type === "income" ? "text-success" : "text-danger"}`;
        amountCell.textContent = formatMoney(item.amount);

        row.append(descriptionCell, categoryCell, accountCell, amountCell);
        return row;
    }

    function openDay(date) {
        const day = calendarData[date];
        if (!day) return;

        title.textContent = financeTranslate("Transactions - {date}", { date });
        summary.textContent = [
            `${financeTranslate("Spending")} ${formatMoney(day.spending)}`,
            `${financeTranslate("Income")} ${formatMoney(day.income)}`,
            `${financeTranslate("Net cash flow")} ${formatMoney(day.net)}`,
        ].join(" / ");
        link.href = day.url || "#";

        const transactions = day.transactions || [];
        empty.classList.toggle("d-none", transactions.length > 0);
        table.classList.toggle("d-none", transactions.length === 0);
        transactionBody.replaceChildren(...transactions.map(transactionRow));

        modal.show();
    }

    root.querySelectorAll("[data-calendar-day-open]").forEach((button) => {
        if (button.dataset.calendarDayOpenReady === "true") return;
        button.dataset.calendarDayOpenReady = "true";
        button.addEventListener("click", () => openDay(button.dataset.calendarDayOpen));
    });

    root.querySelectorAll("[data-calendar-day]").forEach((day) => {
        if (day.dataset.calendarDayReady === "true") return;
        day.dataset.calendarDayReady = "true";
        day.addEventListener("dblclick", (event) => {
            if (event.target.closest("a, button")) return;
            openDay(day.dataset.calendarDay);
        });
    });
}

function setupCalendarHeatmapControls(root = document) {
    const controls =
        root.querySelector("[data-calendar-heatmap-controls]") ||
        document.querySelector("[data-calendar-heatmap-controls]");
    if (!controls) return;
    if (controls.dataset.calendarHeatmapReady === "true") return;
    controls.dataset.calendarHeatmapReady = "true";

    const buttons = Array.from(controls.querySelectorAll("[data-calendar-heatmap]"));
    const days = Array.from(document.querySelectorAll("[data-calendar-day]"));
    const heatmapInputs = Array.from(document.querySelectorAll("[data-calendar-heatmap-input]"));
    const preserveLinks = Array.from(document.querySelectorAll("[data-calendar-preserve-heatmap]"));
    const heatmapClasses = ["calendar-heat-spending", "calendar-heat-income"];

    function applyHeatmap(metric) {
        days.forEach((day) => {
            const heatClass =
                day.dataset[`heatmap${metric[0].toUpperCase()}${metric.slice(1)}Class`] || "calendar-heat-spending";
            const alpha = day.dataset[`heatmap${metric[0].toUpperCase()}${metric.slice(1)}Alpha`] || "0";
            day.classList.remove(...heatmapClasses);
            day.classList.add(heatClass);
            day.style.setProperty("--calendar-heat-alpha", alpha);
        });

        buttons.forEach((button) => {
            const active = button.dataset.calendarHeatmap === metric;
            button.classList.toggle("btn-primary", active);
            button.classList.toggle("btn-outline-secondary", !active);
            button.setAttribute("aria-pressed", active ? "true" : "false");
        });

        heatmapInputs.forEach((input) => {
            input.value = metric;
        });

        preserveLinks.forEach((link) => {
            const url = new URL(link.href, window.location.href);
            url.searchParams.set("heatmap", metric);
            link.href = url.toString();
        });
    }

    buttons.forEach((button) => {
        button.addEventListener("click", () => {
            applyHeatmap(button.dataset.calendarHeatmap || "spending");
        });
    });
}

function setupCalendarAjaxNavigation() {
    if (document.body.dataset.calendarAjaxReady === "true") return;
    document.body.dataset.calendarAjaxReady = "true";

    const dynamicSelector = "[data-calendar-dynamic]";
    let dynamicRefresh = null;

    function destroyDynamicFlatpickr(dynamic) {
        dynamic.querySelectorAll("[data-flatpickr-date], [data-flatpickr-month]").forEach((input) => {
            input.financeFlatpickr?.destroy();
            delete input.financeFlatpickr;
        });
    }

    function calendarRefresh() {
        if (!dynamicRefresh && typeof window.financeApp?.createDynamicPageRefresh === "function") {
            dynamicRefresh = window.financeApp.createDynamicPageRefresh({
                selector: dynamicSelector,
                routeDatasetKey: "calendarUrl",
                loadingClass: "calendar-dynamic-loading",
                historyState: { calendarAjax: true },
                errorMessage: financeTranslate("Calendar refresh failed."),
                missingMessage: financeTranslate("Calendar refresh returned no content."),
                disposeTarget: ({ currentTarget }) => {
                    destroyDynamicFlatpickr(currentTarget);
                },
            });
        }
        return dynamicRefresh;
    }

    document.addEventListener("click", (event) => {
        const link = event.target.closest("[data-calendar-ajax-link]");
        if (!link) return;

        const refresh = calendarRefresh();
        const url = refresh?.url(link.href);
        if (!url) return;

        event.preventDefault();
        refresh.replace(url);
    });

    document.addEventListener("submit", (event) => {
        const form = event.target.closest("[data-calendar-ajax-form]");
        if (!form) return;

        const refresh = calendarRefresh();
        const url = refresh?.url(refresh.formUrl(form));
        if (!url) return;

        event.preventDefault();
        refresh.replace(url);
    });

    window.addEventListener("popstate", () => {
        const refresh = calendarRefresh();
        const url = refresh?.url(window.location.href);
        if (url) refresh.replace(url, { pushState: false });
    });
}

window.financeApp?.registerInitializer("calendar.day-modal", setupCalendarDayModal);
window.financeApp?.registerInitializer("calendar.heatmap-controls", setupCalendarHeatmapControls);
window.financeApp?.registerInitializer("calendar.ajax-navigation", setupCalendarAjaxNavigation);

setupCalendarDayModal();
setupCalendarHeatmapControls();
setupCalendarAjaxNavigation();
