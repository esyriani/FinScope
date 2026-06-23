const dashboardChartUtils = window.financeCharts;
let dashboardCharts = dashboardChartUtils.readJsonScript("dashboard-chart-data", {});
const dashboardTheme = dashboardChartUtils.theme();
const formatDashboardMoney = dashboardChartUtils.formatMoney;
const formatDashboardAxisMoney = dashboardChartUtils.formatAxisMoney;
const dashboardTranslate = dashboardChartUtils.translate;

function dashboardMonthName(monthIndex) {
    return new Date(Date.UTC(2000, monthIndex, 1)).toLocaleString(window.financeLocale || "en-CA", {
        month: "short",
        timeZone: "UTC",
    });
}

function dashboardMonthParts(label) {
    const match = String(label || "").match(/^(\d{4})-(\d{2})$/);
    if (!match) return null;

    const monthIndex = Number(match[2]) - 1;
    if (monthIndex < 0 || monthIndex >= 12) return null;

    return {
        year: match[1],
        month: dashboardMonthName(monthIndex),
    };
}

function dashboardMonthLabels(labels) {
    const parts = labels.map(dashboardMonthParts);
    if (parts.some((part) => !part)) {
        return labels;
    }

    const years = new Set(parts.map((part) => part.year));
    return parts.map((part) => (years.size > 1 ? `${part.month} ${part.year}` : part.month));
}

function dashboardDataPoint(value, itemStyle = {}) {
    return {
        value,
        itemStyle,
    };
}

function dashboardCreateChart(element, option) {
    return dashboardChartUtils.create(element, option, {
        handlerKey: "financeDashboardResizeHandler",
    });
}

function disposeDashboardCharts(root = document) {
    ["spendingIncomeChart", "netChart"].forEach((id) => {
        dashboardChartUtils.dispose(dashboardChartUtils.element(id, root), "financeDashboardResizeHandler");
    });
}

function dashboardSpendingIncomeOption() {
    const labels = dashboardMonthLabels(dashboardCharts.spendingIncomeMonthLabels || []);
    const spendingTotals = dashboardCharts.spendingIncomeSpendingTotals || [];
    const incomeTotals = dashboardCharts.spendingIncomeIncomeTotals || [];

    return {
        textStyle: {
            color: dashboardTheme.text,
        },
        legend: dashboardChartUtils.legend(dashboardTheme),
        tooltip: dashboardChartUtils.tooltip(dashboardTheme, {
            trigger: "axis",
            formatter(items) {
                const rows = items.map(
                    (item) => `${item.marker}${item.seriesName}: ${formatDashboardMoney(item.value)}`
                );
                return [items[0]?.axisValue, ...rows].join("<br>");
            },
        }),
        grid: dashboardChartUtils.baseGrid(),
        xAxis: {
            type: "category",
            boundaryGap: false,
            data: labels,
            axisLine: dashboardChartUtils.axisLine(dashboardTheme),
            axisLabel: dashboardChartUtils.axisLabel(dashboardTheme),
            splitLine: {
                show: false,
            },
        },
        yAxis: {
            type: "value",
            axisLine: dashboardChartUtils.axisLine(dashboardTheme),
            axisLabel: dashboardChartUtils.axisLabel(dashboardTheme, formatDashboardAxisMoney),
            splitLine: dashboardChartUtils.splitLine(dashboardTheme),
        },
        series: [
            {
                name: dashboardTranslate("Spending"),
                type: "line",
                data: spendingTotals,
                itemStyle: {
                    color: dashboardTheme.danger,
                },
                lineStyle: {
                    color: dashboardTheme.danger,
                },
                smooth: true,
                symbolSize: 7,
                emphasis: {
                    focus: "series",
                },
            },
            {
                name: dashboardTranslate("Income and credits"),
                type: "line",
                data: incomeTotals,
                itemStyle: {
                    color: dashboardTheme.success,
                },
                lineStyle: {
                    color: dashboardTheme.success,
                },
                smooth: true,
                symbolSize: 7,
                emphasis: {
                    focus: "series",
                },
            },
        ],
    };
}

function dashboardNetCashflowOption() {
    const labels = dashboardMonthLabels(dashboardCharts.netMonthLabels || []);
    const totals = dashboardCharts.netMonthTotals || [];

    return {
        textStyle: {
            color: dashboardTheme.text,
        },
        tooltip: dashboardChartUtils.tooltip(dashboardTheme, {
            formatter: (params) => `${params.name}: ${formatDashboardMoney(params.value)}`,
        }),
        grid: dashboardChartUtils.baseGrid(),
        xAxis: {
            type: "category",
            data: labels,
            axisLine: dashboardChartUtils.axisLine(dashboardTheme),
            axisLabel: dashboardChartUtils.axisLabel(dashboardTheme),
            splitLine: {
                show: false,
            },
        },
        yAxis: {
            type: "value",
            axisLine: dashboardChartUtils.axisLine(dashboardTheme),
            axisLabel: dashboardChartUtils.axisLabel(dashboardTheme, formatDashboardAxisMoney),
            splitLine: dashboardChartUtils.splitLine(dashboardTheme),
        },
        series: [
            {
                type: "bar",
                data: totals.map((value) =>
                    dashboardDataPoint(value, {
                        color: value >= 0 ? dashboardTheme.success : dashboardTheme.danger,
                        borderRadius: value >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4],
                    })
                ),
                emphasis: {
                    focus: "self",
                },
            },
        ],
    };
}

function renderDashboardCharts(root = document) {
    dashboardCharts = dashboardChartUtils.readJsonScript("dashboard-chart-data", {}, root);

    const spendingIncomeChart = dashboardChartUtils.element("spendingIncomeChart", root);
    if (spendingIncomeChart && dashboardCharts.spendingIncomeMonthLabels?.length > 0) {
        dashboardCreateChart(spendingIncomeChart, dashboardSpendingIncomeOption());
    }

    const netChart = dashboardChartUtils.element("netChart", root);
    if (netChart && dashboardCharts.netMonthLabels?.length > 0) {
        dashboardCreateChart(netChart, dashboardNetCashflowOption());
    }
}

window.financeApp?.registerInitializer("dashboard.charts", renderDashboardCharts);

window.disposeDashboardCharts = disposeDashboardCharts;
renderDashboardCharts();
