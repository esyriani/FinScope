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
            color: comparisonTheme.text
        },
        legend: comparisonChartUtils.legend(comparisonTheme),
        tooltip: comparisonChartUtils.tooltip(comparisonTheme, {
            trigger: "axis",
            formatter(items) {
                const rows = items.map(item => (
                    `${item.marker}${item.seriesName}: ${formatComparisonMoney(item.value)}`
                ));
                return [items[0]?.axisValue, ...rows].join("<br>");
            }
        }),
        grid: comparisonChartUtils.baseGrid(),
        xAxis: {
            type: "category",
            boundaryGap: isBarChart,
            data: comparisonCharts.monthLabels || [],
            axisLine: comparisonChartUtils.axisLine(comparisonTheme),
            axisLabel: comparisonChartUtils.axisLabel(comparisonTheme),
            splitLine: {
                show: false
            }
        },
        yAxis: {
            type: "value",
            axisLine: comparisonChartUtils.axisLine(comparisonTheme),
            axisLabel: comparisonChartUtils.axisLabel(comparisonTheme, formatComparisonAxisMoney),
            splitLine: comparisonChartUtils.splitLine(comparisonTheme)
        },
        series: (comparisonCharts.monthlySpending || []).map((row, index) => ({
            name: String(row.year),
            type: isBarChart ? "bar" : "line",
            data: row.totals || [],
            itemStyle: {
                color: comparisonPalette[index % comparisonPalette.length]
            },
            lineStyle: {
                color: comparisonPalette[index % comparisonPalette.length]
            },
            barMaxWidth: 34,
            smooth: false,
            symbolSize: isBarChart ? 0 : 8,
            emphasis: {
                focus: "series"
            }
        }))
    };
}

function renderComparisonChart() {
    if (!comparisonCharts.monthlySpending?.length) return;

    const element = comparisonChartUtils.element("comparisonChart");
    if (!element) return;
    if (element.dataset.comparisonChartReady === "true") return;
    element.dataset.comparisonChartReady = "true";

    const chart = comparisonChartUtils.create(element, comparisonChartOption());
    if (!chart) return;

    document.querySelectorAll("input[name='comparison_chart_type']").forEach((input) => {
        input.addEventListener("change", () => {
            if (!input.checked) return;
            chart.setOption(comparisonChartOption(input.value), true);
            comparisonChartUtils.resize(chart);
        });
    });
}

window.financeApp?.registerInitializer("comparison.chart", renderComparisonChart);

renderComparisonChart();
