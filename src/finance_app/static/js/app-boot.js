const financeDocument = document.documentElement;

function financeBootJsonDataset(name, fallback) {
    const rawValue = financeDocument.dataset[name];
    if (!rawValue) {
        return fallback;
    }

    try {
        return JSON.parse(rawValue);
    } catch (_error) {
        return fallback;
    }
}

function financeBootSidebarCollapsed() {
    try {
        return localStorage.getItem("finance.sidebarCollapsed") === "true";
    } catch (_error) {
        return false;
    }
}

function financeMoneyNumber(value) {
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

function financeFormatNumberParts(formatter, numberValue) {
    if (typeof formatter.formatToParts !== "function") {
        return formatter.format(numberValue);
    }

    return formatter
        .formatToParts(numberValue)
        .map((part) => (part.type === "group" ? " " : part.value))
        .join("");
}

window.financeLocale = financeDocument.dataset.financeLocale || "en-CA";
window.financeCurrencySymbol = financeDocument.dataset.financeCurrencySymbol || "$";
window.financeI18n = financeBootJsonDataset("financeI18n", {});
window.financeTranslate = function financeTranslate(message, variables) {
    const template = (window.financeI18n && window.financeI18n[message]) || message;
    return Object.entries(variables || {}).reduce(
        (result, entry) => result.replaceAll(`{${entry[0]}}`, String(entry[1])),
        template
    );
};
window.financeFormatMoney = function financeFormatMoney(value, options = {}) {
    const minimumFractionDigits = options.minimumFractionDigits ?? 2;
    const maximumFractionDigits = options.maximumFractionDigits ?? minimumFractionDigits;
    const formatter = new Intl.NumberFormat(window.financeLocale || "en-CA", {
        minimumFractionDigits,
        maximumFractionDigits,
    });
    const numberValue = financeMoneyNumber(value);
    if (numberValue === null) {
        return "";
    }

    const formatted = financeFormatNumberParts(formatter, numberValue);
    return `${formatted} ${window.financeCurrencySymbol || "$"}`.trim();
};
window.financeFormatAxisMoney = function financeFormatAxisMoney(value) {
    return window.financeFormatMoney(value, {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    });
};

if (financeBootSidebarCollapsed()) {
    financeDocument.classList.add("sidebar-collapsed");
}

financeDocument.setAttribute("data-bs-theme", financeDocument.dataset.bsTheme === "dark" ? "dark" : "light");
