function readJsonScript(id, fallback) {
    const node = document.getElementById(id);
    if (!node) return fallback;

    try {
        return JSON.parse(node.textContent);
    } catch {
        return fallback;
    }
}

const comparisonCharts = readJsonScript("comparison-chart-data", {});
const comparisonPalette = ["#0f766e", "#2563eb", "#d97706", "#be123c", "#7c3aed"];

function comparisonCssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

const comparisonTheme = {
    text: comparisonCssVar("--chart-text", "#334155"),
    grid: comparisonCssVar("--chart-grid", "rgba(100, 116, 139, 0.22)"),
    tooltipBg: comparisonCssVar("--chart-tooltip-bg", "#17201d"),
    tooltipText: comparisonCssVar("--chart-tooltip-text", "#ffffff")
};

const comparisonMoneyFormatter = new Intl.NumberFormat(window.financeLocale || "en-CA", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
});

const comparisonAxisMoneyFormatter = new Intl.NumberFormat(window.financeLocale || "en-CA", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0
});

function formatComparisonMoney(value) {
    return comparisonMoneyFormatter.format(Number(value) || 0).replace(/,/g, " ") + " $";
}

function formatComparisonAxisMoney(value) {
    return comparisonAxisMoneyFormatter.format(Number(value) || 0).replace(/,/g, " ") + " $";
}

function comparisonAxisLine() {
    return {
        lineStyle: {
            color: comparisonTheme.grid
        }
    };
}

function comparisonAxisLabel(formatter) {
    return {
        color: comparisonTheme.text,
        formatter
    };
}

function comparisonSplitLine() {
    return {
        lineStyle: {
            color: comparisonTheme.grid
        }
    };
}

function comparisonChartOption(chartType = "line") {
    const isBarChart = chartType === "bar";

    return {
        textStyle: {
            color: comparisonTheme.text
        },
        legend: {
            textStyle: {
                color: comparisonTheme.text
            }
        },
        tooltip: {
            trigger: "axis",
            backgroundColor: comparisonTheme.tooltipBg,
            borderColor: comparisonTheme.grid,
            borderWidth: 1,
            textStyle: {
                color: comparisonTheme.tooltipText
            },
            formatter(items) {
                const rows = items.map(item => (
                    `${item.marker}${item.seriesName}: ${formatComparisonMoney(item.value)}`
                ));
                return [items[0]?.axisValue, ...rows].join("<br>");
            }
        },
        grid: {
            containLabel: true,
            left: 16,
            right: 24,
            top: 32,
            bottom: 24
        },
        xAxis: {
            type: "category",
            boundaryGap: isBarChart,
            data: comparisonCharts.monthLabels || [],
            axisLine: comparisonAxisLine(),
            axisLabel: comparisonAxisLabel(),
            splitLine: {
                show: false
            }
        },
        yAxis: {
            type: "value",
            axisLine: comparisonAxisLine(),
            axisLabel: comparisonAxisLabel(formatComparisonAxisMoney),
            splitLine: comparisonSplitLine()
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
    if (!window.echarts || !comparisonCharts.monthlySpending?.length) return;

    const element = document.getElementById("comparisonChart");
    if (!element) return;

    const chart = echarts.init(element, null, { renderer: "canvas" });
    chart.setOption(comparisonChartOption());

    document.querySelectorAll("input[name='comparison_chart_type']").forEach((input) => {
        input.addEventListener("change", () => {
            if (!input.checked) return;
            chart.setOption(comparisonChartOption(input.value), true);
        });
    });

    window.addEventListener("resize", () => chart.resize());
}

renderComparisonChart();
