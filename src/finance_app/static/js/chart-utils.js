// Shared ECharts helpers for page-specific chart modules.
const financeChartPalette = [
    "#0f766e",
    "#2563eb",
    "#d97706",
    "#be123c",
    "#7c3aed",
    "#16a34a",
    "#0891b2",
    "#ca8a04",
    "#475569",
    "#db2777"
];

function readFinanceChartJsonScript(id, fallback, root = document) {
    const node = root.getElementById ? root.getElementById(id) : root.querySelector(`#${id}`);
    if (!node) return fallback;

    try {
        return JSON.parse(node.textContent);
    } catch {
        return fallback;
    }
}

function financeChartCssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

function financeChartTheme(overrides = {}) {
    return {
        text: financeChartCssVar("--chart-text", "#334155"),
        grid: financeChartCssVar("--chart-grid", "rgba(100, 116, 139, 0.22)"),
        surface: financeChartCssVar("--chart-surface", "#ffffff"),
        tooltipBg: financeChartCssVar("--chart-tooltip-bg", "#17201d"),
        tooltipText: financeChartCssVar("--chart-tooltip-text", "#ffffff"),
        success: financeChartCssVar("--app-success", "#15803d"),
        danger: financeChartCssVar("--app-danger", "#be123c"),
        ...overrides,
    };
}

function financeChartFormatMoney(value) {
    return window.financeFormatMoney
        ? window.financeFormatMoney(value)
        : Number(value || 0).toFixed(2);
}

function financeChartFormatAxisMoney(value) {
    return window.financeFormatAxisMoney
        ? window.financeFormatAxisMoney(value)
        : String(Math.round(Number(value) || 0));
}

function financeChartTranslate(message, variables) {
    return window.financeTranslate ? window.financeTranslate(message, variables) : message;
}

function financeChartAxisLine(theme) {
    return {
        lineStyle: {
            color: theme.grid
        }
    };
}

function financeChartAxisLabel(theme, formatter) {
    return {
        color: theme.text,
        formatter
    };
}

function financeChartSplitLine(theme) {
    return {
        lineStyle: {
            color: theme.grid
        }
    };
}

function financeChartTooltip(theme, extra = {}) {
    return {
        trigger: "item",
        backgroundColor: theme.tooltipBg,
        borderColor: theme.grid,
        borderWidth: 1,
        textStyle: {
            color: theme.tooltipText
        },
        ...extra
    };
}

function financeChartLegend(theme) {
    return {
        textStyle: {
            color: theme.text
        }
    };
}

function financeChartBaseGrid(extra = {}) {
    return {
        containLabel: true,
        left: 16,
        right: 24,
        top: 32,
        bottom: 24,
        ...extra
    };
}

function financeChartElement(id, root = document) {
    return root.getElementById ? root.getElementById(id) : root.querySelector(`#${id}`);
}

function financeChartResize(chart) {
    window.requestAnimationFrame(() => chart.resize());
}

function financeChartObserveResize(chart, element, handlerKey = "financeChartResizeHandler") {
    const existingHandler = element[handlerKey];
    if (existingHandler) {
        window.removeEventListener("resize", existingHandler);
        window.removeEventListener("finance:layoutchange", existingHandler);
    }
    if (element.financeChartResizeObserver) {
        element.financeChartResizeObserver.disconnect();
    }

    const resizeHandler = () => {
        if (element.isConnected) {
            financeChartResize(chart);
        }
    };
    element[handlerKey] = resizeHandler;
    window.addEventListener("resize", resizeHandler);
    window.addEventListener("finance:layoutchange", resizeHandler);

    if (window.ResizeObserver) {
        element.financeChartResizeObserver = new ResizeObserver(() => financeChartResize(chart));
        element.financeChartResizeObserver.observe(element);
    }

    financeChartResize(chart);
}

function financeChartDispose(element, handlerKey = "financeChartResizeHandler") {
    if (!element) return;

    if (element.financeChartResizeObserver) {
        element.financeChartResizeObserver.disconnect();
        delete element.financeChartResizeObserver;
    }

    const resizeHandler = element[handlerKey];
    if (resizeHandler) {
        window.removeEventListener("resize", resizeHandler);
        window.removeEventListener("finance:layoutchange", resizeHandler);
        delete element[handlerKey];
    }

    window.echarts?.getInstanceByDom(element)?.dispose();
}

function financeChartCreate(element, option, options = {}) {
    if (!window.echarts || !element) return null;

    const handlerKey = options.handlerKey || "financeChartResizeHandler";
    financeChartDispose(element, handlerKey);
    const chart = echarts.init(element, null, { renderer: options.renderer || "canvas" });
    chart.setOption(option);
    options.beforeObserve?.(chart);
    financeChartObserveResize(chart, element, handlerKey);
    return chart;
}

window.financeCharts = {
    ...(window.financeCharts || {}),
    axisLabel: financeChartAxisLabel,
    axisLine: financeChartAxisLine,
    baseGrid: financeChartBaseGrid,
    create: financeChartCreate,
    dispose: financeChartDispose,
    element: financeChartElement,
    formatAxisMoney: financeChartFormatAxisMoney,
    formatMoney: financeChartFormatMoney,
    legend: financeChartLegend,
    observeResize: financeChartObserveResize,
    palette: financeChartPalette,
    readJsonScript: readFinanceChartJsonScript,
    resize: financeChartResize,
    splitLine: financeChartSplitLine,
    theme: financeChartTheme,
    tooltip: financeChartTooltip,
    translate: financeChartTranslate,
};
