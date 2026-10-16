import { afterEach, describe, expect, it, vi } from "vitest";

import { createDom, loadScript } from "./support/dom.js";

describe("table export runtime behavior", () => {
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it("exports displayed rows and neutralizes spreadsheet formula prefixes", async () => {
        const dom = createDom(`
            <section class="card">
                <h5 class="section-title">Transactions</h5>
                <table data-export-filename-base="transactions">
                    <thead>
                        <tr>
                            <th>Merchant</th>
                            <th>Amount</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>=Metro</td>
                            <td>12.34</td>
                            <td><button type="button" data-row-action>Open</button></td>
                        </tr>
                        <tr hidden>
                            <td>Hidden merchant</td>
                            <td>99.99</td>
                            <td><button type="button" data-row-action>Open</button></td>
                        </tr>
                    </tbody>
                </table>
            </section>
        `);
        const { document, URL } = dom.window;
        let capturedBlob = null;

        URL.createObjectURL = vi.fn((blob) => {
            capturedBlob = blob;
            return "blob:transactions";
        });
        URL.revokeObjectURL = vi.fn();
        dom.window.HTMLAnchorElement.prototype.click = vi.fn();

        loadScript(dom, "exports.js");

        document.querySelector('[aria-label="Export CSV"]').click();

        expect(URL.createObjectURL).toHaveBeenCalledOnce();
        expect(capturedBlob).not.toBeNull();
        await expect(capturedBlob.text()).resolves.toBe("Merchant,Amount\r\n'=Metro,12.34");
        expect(document.querySelectorAll(".export-toolbar")).toHaveLength(1);
        expect(URL.revokeObjectURL).not.toHaveBeenCalled();

        dom.window.close();
    });

    it("defers blob URL revocation until after the click has consumed the download URL", async () => {
        const dom = createDom(`
            <table data-export-filename-base="transactions">
                <thead><tr><th>Merchant</th></tr></thead>
                <tbody><tr><td>Metro</td></tr></tbody>
            </table>
        `);
        const { document, URL } = dom.window;
        let deferredCleanup = null;

        URL.createObjectURL = vi.fn(() => "blob:transactions");
        URL.revokeObjectURL = vi.fn();
        dom.window.HTMLAnchorElement.prototype.click = vi.fn();
        dom.window.setTimeout = vi.fn((callback, delay) => {
            deferredCleanup = { callback, delay };
            return 1;
        });

        loadScript(dom, "exports.js");

        document.querySelector('[aria-label="Export CSV"]').click();

        expect(dom.window.HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce();
        expect(URL.revokeObjectURL).not.toHaveBeenCalled();
        expect(deferredCleanup.delay).toBe(1000);

        deferredCleanup.callback();

        expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:transactions");

        dom.window.close();
    });

    it("exports rows matched by the visible source table instead of hidden source rows", async () => {
        const dom = createDom(`
            <table id="visible-source" data-no-export>
                <tbody>
                    <tr data-export-row-id="visible"><td>Visible source</td></tr>
                    <tr data-export-row-id="hidden" hidden><td>Hidden source</td></tr>
                </tbody>
            </table>
            <table data-export-filename-base="summary" data-export-visible-source="#visible-source">
                <thead><tr><th>Merchant</th></tr></thead>
                <tbody>
                    <tr data-export-row-id="visible"><td>Metro</td></tr>
                    <tr data-export-row-id="hidden"><td>Pharmacy</td></tr>
                </tbody>
            </table>
        `);
        const { document, URL } = dom.window;
        let capturedBlob = null;

        URL.createObjectURL = vi.fn((blob) => {
            capturedBlob = blob;
            return "blob:summary";
        });
        URL.revokeObjectURL = vi.fn();
        dom.window.HTMLAnchorElement.prototype.click = vi.fn();

        loadScript(dom, "exports.js");

        document.querySelector('[aria-label="Export CSV"]').click();

        await expect(capturedBlob.text()).resolves.toBe("Merchant\r\nMetro");

        dom.window.close();
    });
});

describe("chart export runtime behavior", () => {
    it("exports and expands each chart and restores controls after an AJAX replacement", () => {
        const markup = `
            <div id="dashboard-page">
                <article class="card">
                    <div class="section-title"><h6>Net cash flow</h6></div>
                    <div id="netChart" class="chart-viewport" data-chart-export data-export-title="Net cash flow"><canvas></canvas></div>
                </article>
                <article class="card">
                    <div class="section-title"><h6>Spending vs income</h6></div>
                    <div id="spendingIncomeChart" class="chart-viewport" data-chart-export data-export-title="Spending vs income"><canvas></canvas></div>
                </article>
            </div>
        `;
        const dom = createDom(markup);
        const { document } = dom.window;
        const initializers = new Map();
        const downloads = [];
        const charts = Object.fromEntries(
            ["netChart", "spendingIncomeChart"].map((id) => [
                id,
                {
                    getDataURL: vi.fn(() => `data:image/png;base64,${id}`),
                    resize: vi.fn(),
                },
            ])
        );
        dom.window.financeApp = {
            registerInitializer: (name, initializer) => initializers.set(name, initializer),
        };
        dom.window.echarts = { getInstanceByDom: (element) => charts[element.id] };
        dom.window.requestAnimationFrame = vi.fn((callback) => callback());
        dom.window.HTMLAnchorElement.prototype.click = function () {
            downloads.push({ href: this.href, filename: this.download });
        };
        document.documentElement.style.setProperty("--chart-surface", "#123456");

        loadScript(dom, "exports.js");

        expect(document.querySelectorAll(".chart-export-toolbar")).toHaveLength(2);
        for (const [index, id] of ["netChart", "spendingIncomeChart"].entries()) {
            const element = document.getElementById(id);
            const header = element.closest(".card").querySelector(".section-title");
            expect(header.querySelectorAll("button")).toHaveLength(2);
            header.querySelector('[aria-label="Export PNG"]').click();
            expect(charts[id].getDataURL).toHaveBeenCalledWith({
                type: "png",
                pixelRatio: 2,
                backgroundColor: "#123456",
            });
            expect(downloads[index].href).toBe(`data:image/png;base64,${id}`);
            expect(downloads[index].filename).toMatch(
                new RegExp(
                    `^${index === 0 ? "net-cash-flow" : "spending-vs-income"}-${index + 1}-\\d{4}-\\d{2}-\\d{2}\\.png$`
                )
            );

            header.querySelector(`[aria-label="Expand ${element.dataset.exportTitle}"]`).click();
            expect(document.querySelector("[data-export-expand-body]").firstElementChild).toBe(element);
            expect(element.classList.contains("export-expanded-chart")).toBe(true);
            expect(document.querySelector("[data-export-expand-title]").textContent).toBe(element.dataset.exportTitle);
            expect(charts[id].resize).toHaveBeenCalled();
            document.querySelector("#export-expand-modal .btn-close").click();
            expect(element.closest(".card").querySelector(".section-title")).toBe(header);
            expect(element.classList.contains("export-expanded-chart")).toBe(false);
        }

        initializers.get("exports.charts")(document.getElementById("dashboard-page"));
        expect(document.querySelectorAll(".chart-export-toolbar")).toHaveLength(2);

        const replacement = document.createElement("div");
        replacement.innerHTML = markup;
        const root = replacement.firstElementChild;
        document.getElementById("dashboard-page").replaceWith(root);
        initializers.get("exports.charts")(root);
        expect(root.querySelectorAll(".chart-export-toolbar")).toHaveLength(2);
        expect(root.querySelectorAll('[aria-label="Export PNG"]')).toHaveLength(2);
        expect(root.querySelectorAll('[aria-label^="Expand "]')).toHaveLength(2);

        dom.window.close();
    });
});
