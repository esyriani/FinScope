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
    function formatMoney(value) {
        return window.financeFormatMoney ? window.financeFormatMoney(value) : Number(value || 0).toFixed(2);
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

    root.querySelectorAll("[data-calendar-day]").forEach((day) => {
        if (day.dataset.calendarDayReady === "true") return;
        day.dataset.calendarDayReady = "true";
        day.addEventListener("dblclick", (event) => {
            if (event.target.closest("a, button")) return;
            openDay(day.dataset.calendarDay);
        });
        day.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                openDay(day.dataset.calendarDay);
            }
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

    function calendarUrl(value) {
        const url = new URL(value, window.location.href);
        return url.origin === window.location.origin && url.pathname === "/calendar" ? url : null;
    }

    function setDynamicBusy(busy) {
        const dynamic = document.querySelector(dynamicSelector);
        if (!dynamic) return;
        dynamic.setAttribute("aria-busy", busy ? "true" : "false");
        dynamic.classList.toggle("calendar-dynamic-loading", busy);
    }

    function closeOpenCalendarModals() {
        document.querySelectorAll(".modal.show").forEach((modalElement) => {
            window.bootstrap?.Modal.getInstance(modalElement)?.hide();
        });
    }

    function destroyDynamicFlatpickr(dynamic) {
        dynamic.querySelectorAll("[data-flatpickr-date], [data-flatpickr-month]").forEach((input) => {
            input.financeFlatpickr?.destroy();
            delete input.financeFlatpickr;
        });
    }

    function initializeCalendarDynamic(dynamic) {
        window.financeApp?.runInitializers(dynamic);
    }

    async function replaceCalendarDynamic(url, pushState = true) {
        const currentDynamic = document.querySelector(dynamicSelector);
        if (!currentDynamic) {
            window.location.href = url.toString();
            return;
        }

        setDynamicBusy(true);
        try {
            const response = await fetch(url.toString(), {
                headers: { "X-Requested-With": "XMLHttpRequest" },
            });
            if (!response.ok) throw new Error("Calendar refresh failed.");

            const documentText = await response.text();
            const nextDocument = new DOMParser().parseFromString(documentText, "text/html");
            const nextDynamic = nextDocument.querySelector(dynamicSelector);
            if (!nextDynamic) throw new Error("Calendar refresh returned no content.");

            closeOpenCalendarModals();
            destroyDynamicFlatpickr(currentDynamic);
            currentDynamic.replaceWith(document.importNode(nextDynamic, true));
            if (pushState) {
                window.history.pushState({ calendarAjax: true }, "", url.toString());
            }
            initializeCalendarDynamic(document.querySelector(dynamicSelector));
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
        const link = event.target.closest("[data-calendar-ajax-link]");
        if (!link) return;

        const url = calendarUrl(link.href);
        if (!url) return;

        event.preventDefault();
        replaceCalendarDynamic(url);
    });

    document.addEventListener("submit", (event) => {
        const form = event.target.closest("[data-calendar-ajax-form]");
        if (!form) return;

        const url = calendarUrl(formUrl(form));
        if (!url) return;

        event.preventDefault();
        replaceCalendarDynamic(url);
    });

    window.addEventListener("popstate", () => {
        const url = calendarUrl(window.location.href);
        if (url) replaceCalendarDynamic(url, false);
    });
}

window.financeApp?.registerInitializer("calendar.day-modal", setupCalendarDayModal);
window.financeApp?.registerInitializer("calendar.heatmap-controls", setupCalendarHeatmapControls);
window.financeApp?.registerInitializer("calendar.ajax-navigation", setupCalendarAjaxNavigation);

setupCalendarDayModal();
setupCalendarHeatmapControls();
setupCalendarAjaxNavigation();
