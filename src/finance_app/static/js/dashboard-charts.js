const dashboardChartUtils = window.financeCharts;
let dashboardCharts = dashboardChartUtils.readJsonScript("dashboard-chart-data", {});
const dashboardPalette = dashboardChartUtils.palette;
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

function dashboardDataPoint(value, drilldownUrl, itemStyle = {}) {
    return {
        value,
        drilldownUrl,
        itemStyle,
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
        dataIndex: params.dataIndex,
    });
}

function dashboardRegisterDrilldown(chart) {
    chart.on("click", "series", (params) => {
        dashboardSelectChartPoint(chart, params);
    });
    chart.on("dblclick", "series", (params) => {
        dashboardNavigate(params.data?.drilldownUrl);
    });
}

function dashboardCreateChart(element, option, drilldown = true) {
    return dashboardChartUtils.create(element, option, {
        beforeObserve(chart) {
            if (drilldown) {
                dashboardRegisterDrilldown(chart);
            }
        },
        handlerKey: "financeDashboardResizeHandler",
    });
}

function disposeDashboardCharts(root = document) {
    ["categoryChart", "spendingIncomeChart", "netChart"].forEach((id) => {
        dashboardChartUtils.dispose(dashboardChartUtils.element(id, root), "financeDashboardResizeHandler");
    });
}

function dashboardCategoryBarOption() {
    return {
        color: dashboardPalette,
        textStyle: {
            color: dashboardTheme.text,
        },
        tooltip: dashboardChartUtils.tooltip(dashboardTheme, {
            formatter: (params) => `${params.name}: ${formatDashboardMoney(params.value)}`,
        }),
        grid: dashboardChartUtils.baseGrid({ top: 12 }),
        xAxis: {
            type: "value",
            axisLine: dashboardChartUtils.axisLine(dashboardTheme),
            axisLabel: dashboardChartUtils.axisLabel(dashboardTheme, formatDashboardAxisMoney),
            splitLine: dashboardChartUtils.splitLine(dashboardTheme),
        },
        yAxis: {
            type: "category",
            data: dashboardCharts.categoryLabels || [],
            inverse: true,
            axisLine: dashboardChartUtils.axisLine(dashboardTheme),
            axisLabel: dashboardChartUtils.axisLabel(dashboardTheme),
        },
        series: [
            {
                name: dashboardTranslate("Spending"),
                type: "bar",
                cursor: "pointer",
                data: (dashboardCharts.categoryTotals || []).map((value, index) =>
                    dashboardDataPoint(value, dashboardCharts.categoryUrls?.[index], {
                        color: dashboardPalette[index % dashboardPalette.length],
                        borderRadius: [0, 4, 4, 0],
                    })
                ),
                emphasis: {
                    focus: "self",
                },
            },
        ],
    };
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
                cursor: "pointer",
                data: spendingTotals.map((value, index) =>
                    dashboardDataPoint(value, dashboardCharts.spendingIncomeSpendingUrls?.[index])
                ),
                itemStyle: {
                    color: dashboardTheme.danger,
                },
                lineStyle: {
                    color: dashboardTheme.danger,
                },
                smooth: true,
                symbolSize: 8,
                emphasis: {
                    focus: "series",
                },
            },
            {
                name: dashboardTranslate("Income and credits"),
                type: "line",
                cursor: "pointer",
                data: incomeTotals.map((value, index) =>
                    dashboardDataPoint(value, dashboardCharts.spendingIncomeIncomeUrls?.[index])
                ),
                itemStyle: {
                    color: dashboardTheme.success,
                },
                lineStyle: {
                    color: dashboardTheme.success,
                },
                smooth: true,
                symbolSize: 8,
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
        legend: dashboardChartUtils.legend(dashboardTheme),
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
                cursor: "pointer",
                data: totals.map((value, index) =>
                    dashboardDataPoint(value, dashboardCharts.netMonthUrls?.[index], {
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
    const categoryChart = dashboardChartUtils.element("categoryChart", root);
    if (categoryChart && dashboardCharts.categoryLabels?.length > 0) {
        dashboardCreateChart(categoryChart, dashboardCategoryBarOption());
    }

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
