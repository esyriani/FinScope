const comparisonChartUtils = window.financeCharts;
const comparisonCharts = comparisonChartUtils.readJsonScript("comparison-chart-data", {});
const comparisonPalette = comparisonChartUtils.palette.slice(0, 5);
const comparisonTheme = comparisonChartUtils.theme();
const formatComparisonMoney = comparisonChartUtils.formatMoney;
const formatComparisonAxisMoney = comparisonChartUtils.formatAxisMoney;

function comparisonChartOption(chartType = "line") {
    const isBarChart = chartType === "bar";

    return {
        textStyle: {
            color: comparisonTheme.text,
        },
        legend: comparisonChartUtils.legend(comparisonTheme),
        tooltip: comparisonChartUtils.tooltip(comparisonTheme, {
            trigger: "axis",
            formatter(items) {
                const rows = items.map(
                    (item) => `${item.marker}${item.seriesName}: ${formatComparisonMoney(item.value)}`
                );
                return [items[0]?.axisValue, ...rows].join("<br>");
            },
        }),
        grid: comparisonChartUtils.baseGrid(),
        xAxis: {
            type: "category",
            boundaryGap: isBarChart,
            data: comparisonCharts.monthLabels || [],
            axisLine: comparisonChartUtils.axisLine(comparisonTheme),
            axisLabel: comparisonChartUtils.axisLabel(comparisonTheme),
            splitLine: {
                show: false,
            },
        },
        yAxis: {
            type: "value",
            axisLine: comparisonChartUtils.axisLine(comparisonTheme),
            axisLabel: comparisonChartUtils.axisLabel(comparisonTheme, formatComparisonAxisMoney),
            splitLine: comparisonChartUtils.splitLine(comparisonTheme),
        },
        series: (comparisonCharts.monthlySpending || []).map((row, index) => {
            const color = comparisonSeriesColor(index);
            return {
                name: String(row.year),
                type: isBarChart ? "bar" : "line",
                data: row.totals || [],
                itemStyle: {
                    color,
                },
                lineStyle: {
                    color,
                },
                barMaxWidth: 34,
                smooth: false,
                symbolSize: isBarChart ? 0 : 8,
                emphasis: {
                    focus: "series",
                },
            };
        }),
    };
}

function applyComparisonMonthlyView(view, chart) {
    const visualization = document.querySelector("[data-comparison-monthly-visualization]");
    const chartElement = comparisonChartUtils.element("comparisonChart");
    const tableElement = comparisonChartUtils.element("comparisonMonthlyTable");
    const selectedView = view === "table" || view === "bar" ? view : "line";
    const tableSelected = selectedView === "table";

    if (visualization) {
        visualization.dataset.comparisonMonthlyView = selectedView;
    }

    if (chartElement) {
        chartElement.hidden = tableSelected;
    }

    if (tableElement) {
        tableElement.hidden = !tableSelected;
    }

    if (!tableSelected && chart) {
        chart.setOption(comparisonChartOption(selectedView), true);
        comparisonChartUtils.resize(chart);
    }
}

function setupComparisonMonthlyView(chart) {
    const visualization = document.querySelector("[data-comparison-monthly-visualization]");
    const inputs = Array.from(document.querySelectorAll("input[name='comparison_chart_type']"));
    if (!visualization || !inputs.length) return;

    if (visualization.dataset.comparisonMonthlyViewReady !== "true") {
        visualization.dataset.comparisonMonthlyViewReady = "true";
        inputs.forEach((input) => {
            input.addEventListener("change", () => {
                if (!input.checked) return;
                applyComparisonMonthlyView(input.value, chart);
                window.dispatchEvent(new CustomEvent("finance:layoutchange"));
            });
        });
    }

    applyComparisonMonthlyView(inputs.find((input) => input.checked)?.value || "line", chart);
}

function comparisonBoxplotRows() {
    return (comparisonCharts.monthlySpendingStatistics || []).filter((row) => Array.isArray(row.boxplot));
}

function comparisonSeriesColor(index) {
    return comparisonPalette[index % comparisonPalette.length];
}

function comparisonColorAlpha(color, alpha) {
    return window.echarts?.color?.modifyAlpha ? window.echarts.color.modifyAlpha(color, alpha) : color;
}

function comparisonBoxplotDataItem(row, index) {
    const color = comparisonSeriesColor(index);
    return {
        value: row.boxplot,
        mean: row.statistics?.mean,
        itemStyle: {
            borderColor: color,
            color: comparisonColorAlpha(color, 0.22),
        },
        emphasis: {
            itemStyle: {
                borderColor: color,
            },
        },
    };
}

function comparisonBoxplotValues(data) {
    if (Array.isArray(data)) return data;
    if (Array.isArray(data?.value)) return data.value;
    return [];
}

function comparisonBoxplotOption() {
    const rows = comparisonBoxplotRows();

    return {
        textStyle: {
            color: comparisonTheme.text,
        },
        tooltip: comparisonChartUtils.tooltip(comparisonTheme, {
            trigger: "item",
            formatter(params) {
                const values = comparisonBoxplotValues(params.data);
                const mean = params.data?.mean;
                const labels = [
                    ["Min", values[0]],
                    ["Q1", values[1]],
                    ["Median", values[2]],
                    ["Mean", mean],
                    ["Q3", values[3]],
                    ["Max", values[4]],
                ];
                const rows = labels
                    .filter(([, value]) => value !== null && value !== undefined)
                    .map(
                        ([label, value]) => `${comparisonChartUtils.translate(label)}: ${formatComparisonMoney(value)}`
                    );
                return [params.name, ...rows].join("<br>");
            },
        }),
        grid: comparisonChartUtils.baseGrid({ top: 24 }),
        xAxis: {
            type: "category",
            data: rows.map((row) => String(row.year)),
            axisLine: comparisonChartUtils.axisLine(comparisonTheme),
            axisLabel: comparisonChartUtils.axisLabel(comparisonTheme),
            splitLine: {
                show: false,
            },
        },
        yAxis: {
            type: "value",
            axisLine: comparisonChartUtils.axisLine(comparisonTheme),
            axisLabel: comparisonChartUtils.axisLabel(comparisonTheme, formatComparisonAxisMoney),
            splitLine: comparisonChartUtils.splitLine(comparisonTheme),
        },
        series: [
            {
                name:
                    comparisonCharts.monthlyDistributionLabel ||
                    comparisonChartUtils.translate("Monthly spending distribution"),
                type: "boxplot",
                data: rows.map(comparisonBoxplotDataItem),
            },
        ],
    };
}

function renderComparisonChart() {
    if (!comparisonCharts.monthlySpending?.length) {
        setupComparisonMonthlyView(null);
        return;
    }

    const element = comparisonChartUtils.element("comparisonChart");
    if (!element) return;
    if (element.dataset.comparisonChartReady === "true") {
        setupComparisonMonthlyView(window.echarts?.getInstanceByDom(element));
        return;
    }

    const chart = comparisonChartUtils.create(element, comparisonChartOption());
    setupComparisonMonthlyView(chart);
    if (!chart) {
        element.dataset.comparisonChartReady = "true";
        return;
    }

    element.dataset.comparisonChartReady = "true";
}

function renderComparisonBoxplotChart() {
    if (!comparisonBoxplotRows().length) return;

    const element = comparisonChartUtils.element("comparisonBoxplotChart");
    if (!element) return;
    if (element.dataset.comparisonBoxplotReady === "true") return;
    element.dataset.comparisonBoxplotReady = "true";

    comparisonChartUtils.create(element, comparisonBoxplotOption(), { handlerKey: "comparisonBoxplotResizeHandler" });
}

window.financeApp?.registerInitializer("comparison.chart", renderComparisonChart);
window.financeApp?.registerInitializer("comparison.boxplot-chart", renderComparisonBoxplotChart);

renderComparisonChart();
renderComparisonBoxplotChart();
