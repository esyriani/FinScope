import { afterEach, describe, expect, it, vi } from "vitest";

import { createDom, flushAsync, loadScript } from "./support/dom.js";

describe("rule preview runtime behavior", () => {
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it("renders fetched preview rows and handles blank keywords without a request", async () => {
        const dom = createDom(`
            <form data-rule-editor data-rule-preview-url="/rules/preview">
                <input name="keyword" value="METRO">
                <section data-rule-preview>
                    <p data-rule-preview-status></p>
                    <div data-rule-preview-list></div>
                    <button type="button" data-rule-preview-refresh>Refresh</button>
                </section>
            </form>
        `);
        const { document } = dom.window;
        dom.window.fetch = vi.fn(async () => ({
            ok: true,
            json: async () => ({
                ok: true,
                match_count: 2,
                transactions: [
                    {
                        description: "Metro Grocery",
                        tx_date: "2026-09-01",
                        current_category: "UNKNOWN",
                        amount_display: "$12.34",
                    },
                    {
                        description: "Metro Market",
                        tx_date: "2026-09-03",
                        current_category: "Food",
                        amount_display: "$20.00",
                    },
                ],
            }),
        }));

        loadScript(dom, "core.js");
        loadScript(dom, "rules.js");

        document.querySelector("[data-rule-preview-refresh]").click();
        await flushAsync();

        expect(dom.window.fetch).toHaveBeenCalledOnce();
        expect(document.querySelector("[data-rule-preview-status]").textContent).toBe(
            "2 active matching transactions."
        );
        expect([...document.querySelectorAll(".rule-preview-description")].map((node) => node.textContent)).toEqual([
            "Metro Grocery",
            "Metro Market",
        ]);

        document.querySelector("[name='keyword']").value = "";
        document.querySelector("[data-rule-preview-refresh]").click();
        await flushAsync();

        expect(dom.window.fetch).toHaveBeenCalledOnce();
        expect(document.querySelector("[data-rule-preview-status]").textContent).toBe(
            "Enter a keyword to preview matches."
        );
        expect(document.querySelector("[data-rule-preview-list]").children).toHaveLength(0);

        dom.window.close();
    });
});
