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
