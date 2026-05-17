function readJsonScript(id, fallback) {
    const node = document.getElementById(id);
    if (!node) return fallback;

    try {
        return JSON.parse(node.textContent);
    } catch {
        return fallback;
    }
}

const dashboardCharts = readJsonScript("dashboard-chart-data", {});
const dashboardPalette = [
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

function dashboardCssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

const dashboardTheme = {
    text: dashboardCssVar("--chart-text", "#334155"),
    grid: dashboardCssVar("--chart-grid", "rgba(100, 116, 139, 0.22)"),
    surface: dashboardCssVar("--chart-surface", "#ffffff"),
    tooltipBg: dashboardCssVar("--chart-tooltip-bg", "#17201d"),
    tooltipText: dashboardCssVar("--chart-tooltip-text", "#ffffff"),
    success: dashboardCssVar("--app-success", "#15803d"),
    danger: dashboardCssVar("--app-danger", "#be123c")
};

const dashboardMoneyFormatter = new Intl.NumberFormat(window.financeLocale || "en-CA", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
});

const dashboardAxisMoneyFormatter = new Intl.NumberFormat(window.financeLocale || "en-CA", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0
});

function formatDashboardMoney(value) {
    return dashboardMoneyFormatter.format(Number(value) || 0).replace(/,/g, " ") + " $";
}

function formatDashboardAxisMoney(value) {
    return dashboardAxisMoneyFormatter.format(Number(value) || 0).replace(/,/g, " ") + " $";
}

function dashboardTranslate(message, variables) {
    return window.financeTranslate ? window.financeTranslate(message, variables) : message;
}

function dashboardMonthName(monthIndex) {
    return new Date(Date.UTC(2000, monthIndex, 1)).toLocaleString(
        window.financeLocale || "en-CA",
        { month: "short", timeZone: "UTC" }
    );
}

function dashboardMonthParts(label) {
    const match = String(label || "").match(/^(\d{4})-(\d{2})$/);
    if (!match) return null;

    const monthIndex = Number(match[2]) - 1;
    if (monthIndex < 0 || monthIndex >= 12) return null;

    return {
        year: match[1],
        month: dashboardMonthName(monthIndex)
    };
}

function dashboardMonthLabels(labels) {
    const parts = labels.map(dashboardMonthParts);
    if (parts.some(part => !part)) {
        return labels;
    }

    const years = new Set(parts.map(part => part.year));
    return parts.map(part => years.size > 1 ? `${part.month} ${part.year}` : part.month);
}

function dashboardAxisLine() {
    return {
        lineStyle: {
            color: dashboardTheme.grid
        }
    };
}

function dashboardAxisLabel(formatter) {
    return {
        color: dashboardTheme.text,
        formatter
    };
}

function dashboardSplitLine() {
    return {
        lineStyle: {
            color: dashboardTheme.grid
        }
    };
}

function dashboardTooltip(extra = {}) {
    return {
        trigger: "item",
        backgroundColor: dashboardTheme.tooltipBg,
        borderColor: dashboardTheme.grid,
        borderWidth: 1,
        textStyle: {
            color: dashboardTheme.tooltipText
        },
        ...extra
    };
}

function dashboardLegend() {
    return {
        textStyle: {
            color: dashboardTheme.text
        }
    };
}

function dashboardBaseGrid(extra = {}) {
    return {
        containLabel: true,
        left: 16,
        right: 24,
        top: 32,
        bottom: 24,
        ...extra
    };
}

function dashboardChartElement(id) {
    return document.getElementById(id);
}

function dashboardDataPoint(value, drilldownUrl, itemStyle = {}) {
    return {
        value,
        drilldownUrl,
        itemStyle
    };
}

function dashboardNavigate(url) {
    if (url) {
        window.location.href = url;
    }
}

function dashboardClearDomSelection(element) {
    const scope = element.closest("[data-dashboard-drilldown-scope]");
    if (!scope) return;

    scope.querySelectorAll(".dashboard-drilldown-selected").forEach((selectedElement) => {
        if (selectedElement !== element) {
            window.echarts?.getInstanceByDom(selectedElement)?.dispatchAction({ type: "downplay" });
            selectedElement.classList.remove("dashboard-drilldown-selected");
        }
    });
}

function dashboardSelectChartPoint(chart, params) {
    const element = chart.getDom();
    dashboardClearDomSelection(element);
    element.classList.add("dashboard-drilldown-selected");
    chart.dispatchAction({ type: "downplay" });
    chart.dispatchAction({
        type: "highlight",
        seriesIndex: params.seriesIndex,
        dataIndex: params.dataIndex
    });
}

function dashboardRegisterDrilldown(chart) {
    chart.on("click", "series", params => {
        dashboardSelectChartPoint(chart, params);
    });
    chart.on("dblclick", "series", params => {
        dashboardNavigate(params.data?.drilldownUrl);
    });
}

function dashboardResizeChart(chart) {
    window.requestAnimationFrame(() => chart.resize());
}

function dashboardObserveChartResize(chart, element) {
    window.addEventListener("resize", () => dashboardResizeChart(chart));
    window.addEventListener("finance:layoutchange", () => dashboardResizeChart(chart));

    if (window.ResizeObserver) {
        element.financeResizeObserver = new ResizeObserver(() => dashboardResizeChart(chart));
        element.financeResizeObserver.observe(element);
    }

    dashboardResizeChart(chart);
}

function dashboardCreateChart(element, option, drilldown = true) {
    if (!window.echarts || !element) return null;

    const chart = echarts.init(element, null, { renderer: "canvas" });
    chart.setOption(option);
    if (drilldown) {
        dashboardRegisterDrilldown(chart);
    }

    dashboardObserveChartResize(chart, element);
    return chart;
}

function dashboardCategoryBarOption() {
    return {
        color: dashboardPalette,
        textStyle: {
            color: dashboardTheme.text
        },
        tooltip: dashboardTooltip({
            formatter: params => `${params.name}: ${formatDashboardMoney(params.value)}`
        }),
        grid: dashboardBaseGrid({ top: 12 }),
        xAxis: {
            type: "value",
            axisLine: dashboardAxisLine(),
            axisLabel: dashboardAxisLabel(formatDashboardAxisMoney),
            splitLine: dashboardSplitLine()
        },
        yAxis: {
            type: "category",
            data: dashboardCharts.categoryLabels || [],
            inverse: true,
            axisLine: dashboardAxisLine(),
            axisLabel: dashboardAxisLabel()
        },
        series: [
            {
                name: dashboardTranslate("Spending"),
                type: "bar",
                cursor: "pointer",
                data: (dashboardCharts.categoryTotals || []).map((value, index) => (
                    dashboardDataPoint(value, dashboardCharts.categoryUrls?.[index], {
                        color: dashboardPalette[index % dashboardPalette.length],
                        borderRadius: [0, 4, 4, 0]
                    })
                )),
                emphasis: {
                    focus: "self"
                }
            }
        ]
    };
}

function dashboardSpendingIncomeOption() {
    const labels = dashboardMonthLabels(dashboardCharts.spendingIncomeMonthLabels || []);
    const spendingTotals = dashboardCharts.spendingIncomeSpendingTotals || [];
    const incomeTotals = dashboardCharts.spendingIncomeIncomeTotals || [];

    return {
        textStyle: {
            color: dashboardTheme.text
        },
        legend: dashboardLegend(),
        tooltip: dashboardTooltip({
            trigger: "axis",
            formatter(items) {
                const rows = items.map(item => (
                    `${item.marker}${item.seriesName}: ${formatDashboardMoney(item.value)}`
                ));
                return [items[0]?.axisValue, ...rows].join("<br>");
            }
        }),
        grid: dashboardBaseGrid(),
        xAxis: {
            type: "category",
            boundaryGap: false,
            data: labels,
            axisLine: dashboardAxisLine(),
            axisLabel: dashboardAxisLabel(),
            splitLine: {
                show: false
            }
        },
        yAxis: {
            type: "value",
            axisLine: dashboardAxisLine(),
            axisLabel: dashboardAxisLabel(formatDashboardAxisMoney),
            splitLine: dashboardSplitLine()
        },
        series: [
            {
                name: dashboardTranslate("Spending"),
                type: "line",
                cursor: "pointer",
                data: spendingTotals.map((value, index) => (
                    dashboardDataPoint(value, dashboardCharts.spendingIncomeSpendingUrls?.[index])
                )),
                itemStyle: {
                    color: dashboardTheme.danger
                },
                lineStyle: {
                    color: dashboardTheme.danger
                },
                smooth: true,
                symbolSize: 8,
                emphasis: {
                    focus: "series"
                }
            },
            {
                name: dashboardTranslate("Income and credits"),
                type: "line",
                cursor: "pointer",
                data: incomeTotals.map((value, index) => (
                    dashboardDataPoint(value, dashboardCharts.spendingIncomeIncomeUrls?.[index])
                )),
                itemStyle: {
                    color: dashboardTheme.success
                },
                lineStyle: {
                    color: dashboardTheme.success
                },
                smooth: true,
                symbolSize: 8,
                emphasis: {
                    focus: "series"
                }
            }
        ]
    };
}

function dashboardNetCashflowOption() {
    const labels = dashboardMonthLabels(dashboardCharts.netMonthLabels || []);
    const totals = dashboardCharts.netMonthTotals || [];

    return {
        textStyle: {
            color: dashboardTheme.text
        },
        legend: dashboardLegend(),
        tooltip: dashboardTooltip({
            formatter: params => `${params.name}: ${formatDashboardMoney(params.value)}`
        }),
        grid: dashboardBaseGrid(),
        xAxis: {
            type: "category",
            data: labels,
            axisLine: dashboardAxisLine(),
            axisLabel: dashboardAxisLabel(),
            splitLine: {
                show: false
            }
        },
        yAxis: {
            type: "value",
            axisLine: dashboardAxisLine(),
            axisLabel: dashboardAxisLabel(formatDashboardAxisMoney),
            splitLine: dashboardSplitLine()
        },
        series: [
            {
                type: "bar",
                cursor: "pointer",
                data: totals.map((value, index) => (
                    dashboardDataPoint(value, dashboardCharts.netMonthUrls?.[index], {
                        color: value >= 0 ? dashboardTheme.success : dashboardTheme.danger,
                        borderRadius: value >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4]
                    })
                )),
                emphasis: {
                    focus: "self"
                }
            }
        ]
    };
}

function renderDashboardCharts() {
    const categoryChart = dashboardChartElement("categoryChart");
    if (categoryChart && dashboardCharts.categoryLabels?.length > 0) {
        dashboardCreateChart(categoryChart, dashboardCategoryBarOption());
    }

    const spendingIncomeChart = dashboardChartElement("spendingIncomeChart");
    if (spendingIncomeChart && dashboardCharts.spendingIncomeMonthLabels?.length > 0) {
        dashboardCreateChart(spendingIncomeChart, dashboardSpendingIncomeOption());
    }

    const netChart = dashboardChartElement("netChart");
    if (netChart && dashboardCharts.netMonthLabels?.length > 0) {
        dashboardCreateChart(netChart, dashboardNetCashflowOption());
    }
}

renderDashboardCharts();
