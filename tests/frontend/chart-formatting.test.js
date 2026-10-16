import { describe, expect, it, vi } from "vitest";

import { createDom, loadScript } from "./support/dom.js";

describe("shared financial formatting", () => {
    it("preserves locale separators, currency placement, signs and axis precision", () => {
        for (const [locale, money, axis, percent] of [
            ["en-CA", "-$1,234.56", "-$1,235", "+12.3%"],
            ["fr-CA", "-1\u00a0234,56 $", "-1\u00a0235 $", "+12,3 %"],
        ]) {
            const dom = createDom();
            dom.window.document.documentElement.dataset.financeLocale = locale;
            loadScript(dom, "app-boot.js");
            loadScript(dom, "chart-utils.js");

            expect(dom.window.financeCharts.formatMoney("-1234.56")).toBe(money);
            expect(dom.window.financeCharts.formatAxisMoney("-1234.56")).toBe(axis);
            expect(dom.window.financeFormatPercent(12.34, { signDisplay: "always" })).toBe(percent);
            expect(dom.window.financeFormatMoney(0)).toBe(locale === "en-CA" ? "$0.00" : "0,00 $");

            dom.window.close();
        }
    });

    it("does not turn absent, malformed or nonfinite amounts into zero, including chart fallbacks", () => {
        const dom = createDom();
        loadScript(dom, "chart-utils.js");

        for (const bootLoaded of [false, true]) {
            if (bootLoaded) loadScript(dom, "app-boot.js");
            for (const value of [null, undefined, "", "  ", "not-money", NaN, Infinity, -Infinity, true, {}, []]) {
                expect(dom.window.financeCharts.formatMoney(value)).toBe("");
                expect(dom.window.financeCharts.formatAxisMoney(value)).toBe("");
                if (bootLoaded) {
                    expect(dom.window.financeFormatNumber(value)).toBe("");
                    expect(dom.window.financeFormatPercent(value)).toBe("");
                }
            }
        }

        dom.window.close();
    });
});

describe("chart presentation behavior", () => {
    it("uses whole-dollar axes and escaped, cent-precision tooltips in each analytics page", () => {
        for (const [script, dataId, chartId, initializer] of [
            ["dashboard-charts.js", "dashboard-chart-data", "spendingIncomeChart", null],
            ["dashboard-charts.js", "dashboard-chart-data", "netChart", null],
            ["comparison-charts.js", "comparison-chart-data", "comparisonChart", "comparison.chart"],
            ["reports-charts.js", "reports-overview-chart-data", "reportsMonthlyChart", null],
            ["reports-charts.js", "reports-overview-chart-data", "reportsCategoryChart", null],
        ]) {
            const dom = createDom(
                `<div id="${chartId}"></div><script type="application/json" id="${dataId}"></script>`
            );
            const { document } = dom.window;
            document.getElementById(dataId).textContent = JSON.stringify({
                spendingIncomeMonthLabels: ["2025-12"],
                spendingIncomeSpendingTotals: [1234.56],
                spendingIncomeIncomeTotals: [1000],
                netMonthLabels: ["2025-12"],
                netMonthTotals: [-234.56],
                monthLabels: ["Dec"],
                monthlySpending: [{ year: 2025, totals: [1234.56] }],
                monthlyLabels: ["2025-12"],
                categoryLabels: ["Food"],
                categoryValues: [1234.56],
            });
            let option;
            const initializers = new Map();
            dom.window.financeApp = { registerInitializer: (name, fn) => initializers.set(name, fn) };
            dom.window.requestAnimationFrame = vi.fn();
            dom.window.setTimeout = vi.fn();
            dom.window.echarts = {
                getInstanceByDom: () => null,
                init: () => ({
                    setOption: (value) => {
                        option = value;
                    },
                }),
            };
            loadScript(dom, "app-boot.js");
            loadScript(dom, "chart-utils.js");
            loadScript(dom, script);
            if (initializer) initializers.get(initializer)();

            const amountAxis = option.yAxis.type === "value" ? option.yAxis : option.xAxis;
            expect(amountAxis.axisLabel.formatter(1234.56)).toBe("$1,235");
            const tooltip = document.createElement("div");
            const item = {
                axisValue: '<img src=x onerror="alert(1)">',
                seriesName: "<script>bad()</script>&",
                name: "<script>bad()</script>&",
                value: 1234.56,
            };
            tooltip.innerHTML = option.tooltip.formatter(option.tooltip.trigger === "axis" ? [item] : item);
            expect(tooltip.querySelector("img, script")).toBeNull();
            if (option.tooltip.trigger === "axis") expect(tooltip.textContent).toContain(item.axisValue);
            expect(tooltip.textContent).toContain("<script>bad()</script>&: $1,234.56");

            dom.window.close();
        }
    });

    it("escapes tooltip values as well as labels while preserving trusted chart markers", () => {
        const dom = createDom();
        loadScript(dom, "chart-utils.js");
        const tooltip = dom.window.document.createElement("div");
        tooltip.innerHTML = dom.window.financeCharts.tooltipLine(
            '<img src=x onerror="bad()">&',
            "<script>bad()</script>'\"",
            '<span class="marker"></span>'
        );

        expect(tooltip.querySelector("img, script")).toBeNull();
        expect(tooltip.querySelectorAll(".marker")).toHaveLength(1);
        expect(tooltip.textContent).toBe('<img src=x onerror="bad()">&: <script>bad()</script>\'"');
        dom.window.close();
    });

    it("refreshes after layout changes without resizing hidden or detached charts", () => {
        const dom = createDom('<div id="chart"></div>');
        const element = dom.window.document.getElementById("chart");
        const chart = { getDom: () => element, resize: vi.fn(), getZr: () => ({ refresh: repaint }) };
        const repaint = vi.fn();
        dom.window.requestAnimationFrame = (callback) => callback();
        dom.window.setTimeout = vi.fn();
        element.getBoundingClientRect = () => ({ width: 400, height: 200 });
        loadScript(dom, "chart-utils.js");
        dom.window.financeCharts.observeResize(chart, element);
        chart.resize.mockClear();
        repaint.mockClear();

        dom.window.dispatchEvent(new dom.window.Event("finance:layoutchange"));
        expect(chart.resize).toHaveBeenCalledOnce();
        expect(repaint).toHaveBeenCalledOnce();
        element.getBoundingClientRect = () => ({ width: 0, height: 0 });
        dom.window.dispatchEvent(new dom.window.Event("finance:layoutchange"));
        element.remove();
        dom.window.dispatchEvent(new dom.window.Event("pageshow"));
        expect(chart.resize).toHaveBeenCalledOnce();
        dom.window.close();
    });
});
