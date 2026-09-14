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

function financeLanguage() {
    return String(window.financeLocale || "en-CA")
        .split(/[-_]/)[0]
        .toLowerCase();
}

function financeFormatCurrencyText(numberValue, formattedNumber) {
    const symbol = window.financeCurrencySymbol || "$";
    const sign = numberValue < 0 ? "-" : "";
    const absoluteNumber = formattedNumber.replace(/^-/, "");
    if (!symbol) {
        return `${sign}${absoluteNumber}`;
    }
    if (financeLanguage() === "fr") {
        return `${sign}${absoluteNumber} ${symbol}`.trim();
    }
    return `${sign}${symbol}${absoluteNumber}`.trim();
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
window.financeFormatNumber = function financeFormatNumber(value, options = {}) {
    const minimumFractionDigits = options.minimumFractionDigits ?? 0;
    const maximumFractionDigits = options.maximumFractionDigits ?? minimumFractionDigits;
    const formatter = new Intl.NumberFormat(window.financeLocale || "en-CA", {
        minimumFractionDigits,
        maximumFractionDigits,
    });
    const numberValue = financeMoneyNumber(value);
    if (numberValue === null) {
        return "";
    }

    const formatted = formatter.format(Math.abs(numberValue));
    const sign = options.signDisplay === "always" && numberValue > 0 ? "+" : numberValue < 0 ? "-" : "";
    return `${sign}${formatted}`;
};
window.financeFormatMoney = function financeFormatMoney(value, options = {}) {
    const minimumFractionDigits = options.minimumFractionDigits ?? 2;
    const maximumFractionDigits = options.maximumFractionDigits ?? minimumFractionDigits;
    const numberValue = financeMoneyNumber(value);
    if (numberValue === null) {
        return "";
    }

    const formatted = window.financeFormatNumber(Math.abs(numberValue), {
        minimumFractionDigits,
        maximumFractionDigits,
    });
    return financeFormatCurrencyText(numberValue, formatted);
};
window.financeFormatPercent = function financeFormatPercent(value, options = {}) {
    const minimumFractionDigits = options.minimumFractionDigits ?? 1;
    const maximumFractionDigits = options.maximumFractionDigits ?? minimumFractionDigits;
    const formatted = window.financeFormatNumber(value, {
        minimumFractionDigits,
        maximumFractionDigits,
        signDisplay: options.signDisplay,
    });
    if (!formatted) {
        return "";
    }
    return financeLanguage() === "fr" ? `${formatted} %` : `${formatted}%`;
};
window.financeFormatDate = function financeFormatDate(value) {
    if (!value) {
        return "";
    }
    const date = new Date(`${value}T00:00:00`);
    if (Number.isNaN(date.getTime())) {
        return String(value);
    }
    return new Intl.DateTimeFormat(window.financeLocale || "en-CA", {
        day: "numeric",
        month: "short",
        year: "numeric",
    }).format(date);
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
