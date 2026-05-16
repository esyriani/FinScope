function setupCalendarDayModal() {
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
    const moneyFormatter = new Intl.NumberFormat(window.financeLocale || "en-CA", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });

    function formatMoney(value) {
        return moneyFormatter.format(Number(value) || 0).replace(/,/g, " ") + " $";
    }

    function escapeHtmlLocal(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function openDay(date) {
        const day = calendarData[date];
        if (!day) return;

        title.textContent = financeTranslate("Transactions - {date}", { date });
        summary.textContent = [
            `${financeTranslate("Spending")} ${formatMoney(day.spending)}`,
            `${financeTranslate("Income")} ${formatMoney(day.income)}`,
            `${financeTranslate("Net cash flow")} ${formatMoney(day.net)}`
        ].join(" / ");
        link.href = day.url || "#";

        const transactions = day.transactions || [];
        empty.classList.toggle("d-none", transactions.length > 0);
        table.classList.toggle("d-none", transactions.length === 0);
        transactionBody.innerHTML = transactions.map((item) => `
            <tr>
                <td><a class="text-reset text-decoration-none" href="${escapeHtmlLocal(item.url)}">${escapeHtmlLocal(item.description)}</a></td>
                <td>${escapeHtmlLocal(item.category)}</td>
                <td>${escapeHtmlLocal(item.accountName)}</td>
                <td class="text-end ${item.type === "income" ? "text-success" : "text-danger"}">${formatMoney(item.amount)}</td>
            </tr>
        `).join("");

        modal.show();
    }

    document.querySelectorAll("[data-calendar-day]").forEach((day) => {
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

setupCalendarDayModal();

function setupCalendarHeatmapControls() {
    const controls = document.querySelector("[data-calendar-heatmap-controls]");
    if (!controls) return;

    const buttons = Array.from(controls.querySelectorAll("[data-calendar-heatmap]"));
    const days = Array.from(document.querySelectorAll("[data-calendar-day]"));
    const heatmapInputs = Array.from(document.querySelectorAll("[data-calendar-heatmap-input]"));
    const preserveLinks = Array.from(document.querySelectorAll("[data-calendar-preserve-heatmap]"));
    const heatmapClasses = ["calendar-heat-spending", "calendar-heat-income"];

    function applyHeatmap(metric) {
        days.forEach((day) => {
            const heatClass = day.dataset[`heatmap${metric[0].toUpperCase()}${metric.slice(1)}Class`] || "calendar-heat-spending";
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

setupCalendarHeatmapControls();
