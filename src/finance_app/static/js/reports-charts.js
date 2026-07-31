const reportsChartUtils = window.financeCharts;
let reportsCharts = reportsChartUtils.readJsonScript("reports-overview-chart-data", {});
const reportsPalette = reportsChartUtils.palette;
const reportsTheme = reportsChartUtils.theme();
const formatReportsMoney = reportsChartUtils.formatMoney;
const formatReportsAxisMoney = reportsChartUtils.formatAxisMoney;
const reportsChartsTranslate = reportsChartUtils.translate;

function reportsMonthName(monthIndex) {
    return new Date(Date.UTC(2000, monthIndex, 1)).toLocaleString(window.financeLocale || "en-CA", {
        month: "short",
        timeZone: "UTC",
    });
}

function reportsMonthLabel(label) {
    const match = String(label || "").match(/^(\d{4})-(\d{2})$/);
    if (!match) return label;

    const monthIndex = Number(match[2]) - 1;
    if (monthIndex < 0 || monthIndex >= 12) return label;
    return `${reportsMonthName(monthIndex)} ${match[1]}`;
}

function reportsCreateChart(element, option) {
    return reportsChartUtils.create(element, option, {
        handlerKey: "financeReportsResizeHandler",
    });
}

function reportsMonthlyOption() {
    const labels = (reportsCharts.monthlyLabels || []).map(reportsMonthLabel);
    return {
        textStyle: {
            color: reportsTheme.text,
        },
        legend: reportsChartUtils.legend(reportsTheme),
        tooltip: reportsChartUtils.tooltip(reportsTheme, {
            trigger: "axis",
            formatter(items) {
                const rows = items.map((item) => `${item.marker}${item.seriesName}: ${formatReportsMoney(item.value)}`);
                return [items[0]?.axisValue, ...rows].join("<br>");
            },
        }),
        grid: reportsChartUtils.baseGrid(),
        xAxis: {
            type: "category",
            data: labels,
            axisLine: reportsChartUtils.axisLine(reportsTheme),
            axisLabel: reportsChartUtils.axisLabel(reportsTheme),
            splitLine: {
                show: false,
            },
        },
        yAxis: {
            type: "value",
            axisLine: reportsChartUtils.axisLine(reportsTheme),
            axisLabel: reportsChartUtils.axisLabel(reportsTheme, formatReportsAxisMoney),
            splitLine: reportsChartUtils.splitLine(reportsTheme),
        },
        series: [
            {
                name: reportsChartsTranslate("Spending"),
                type: "bar",
                data: reportsCharts.monthlySpending || [],
                itemStyle: {
                    color: reportsTheme.danger,
                },
            },
            {
                name: reportsChartsTranslate("Income and credits"),
                type: "bar",
                data: reportsCharts.monthlyIncome || [],
                itemStyle: {
                    color: reportsTheme.success,
                },
            },
            {
                name: reportsChartsTranslate("Net cash flow"),
                type: "line",
                data: reportsCharts.monthlyNet || [],
                itemStyle: {
                    color: reportsPalette[1],
                },
                lineStyle: {
                    color: reportsPalette[1],
                },
                smooth: true,
            },
        ],
    };
}

function reportsBreakdownOption(labels, values, name) {
    return {
        color: reportsPalette,
        textStyle: {
            color: reportsTheme.text,
        },
        tooltip: reportsChartUtils.tooltip(reportsTheme, {
            formatter: (params) => `${params.name}: ${formatReportsMoney(params.value)}`,
        }),
        grid: reportsChartUtils.baseGrid({ top: 12 }),
        xAxis: {
            type: "value",
            axisLine: reportsChartUtils.axisLine(reportsTheme),
            axisLabel: reportsChartUtils.axisLabel(reportsTheme, formatReportsAxisMoney),
            splitLine: reportsChartUtils.splitLine(reportsTheme),
        },
        yAxis: {
            type: "category",
            data: labels,
            inverse: true,
            axisLine: reportsChartUtils.axisLine(reportsTheme),
            axisLabel: reportsChartUtils.axisLabel(reportsTheme),
        },
        series: [
            {
                name,
                type: "bar",
                data: values.map((value, index) => ({
                    value,
                    itemStyle: {
                        color: reportsPalette[index % reportsPalette.length],
                        borderRadius: [0, 4, 4, 0],
                    },
                })),
                emphasis: {
                    focus: "self",
                },
            },
        ],
    };
}

function renderReportsCharts(root = document) {
    reportsCharts = reportsChartUtils.readJsonScript("reports-overview-chart-data", {}, root);

    const monthlyChart = reportsChartUtils.element("reportsMonthlyChart", root);
    if (monthlyChart && reportsCharts.monthlyLabels?.length > 0) {
        reportsCreateChart(monthlyChart, reportsMonthlyOption());
    }

    const categoryChart = reportsChartUtils.element("reportsCategoryChart", root);
    if (categoryChart && reportsCharts.categoryLabels?.length > 0) {
        reportsCreateChart(
            categoryChart,
            reportsBreakdownOption(
                reportsCharts.categoryLabels,
                reportsCharts.categoryValues || [],
                reportsChartsTranslate("Categories")
            )
        );
    }

    const tagChart = reportsChartUtils.element("reportsTagChart", root);
    if (tagChart && reportsCharts.tagLabels?.length > 0) {
        reportsCreateChart(
            tagChart,
            reportsBreakdownOption(
                reportsCharts.tagLabels,
                reportsCharts.tagValues || [],
                reportsChartsTranslate("Tags")
            )
        );
    }

    const compositionChart = reportsChartUtils.element("reportsCompositionChart", root);
    if (compositionChart && reportsCharts.compositionLabels?.length > 0) {
        reportsCreateChart(
            compositionChart,
            reportsBreakdownOption(
                reportsCharts.compositionLabels,
                reportsCharts.compositionValues || [],
                reportsChartsTranslate(reportsCharts.compositionName || "Composition")
            )
        );
    }
}

window.financeApp?.registerInitializer("reports.charts", renderReportsCharts);
renderReportsCharts();
