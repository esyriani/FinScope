import { afterEach, describe, expect, it, vi } from "vitest";

import { createDom, dispatch, flushAsync, loadScript } from "./support/dom.js";

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

    it("shows an inline modal error instead of submitting reimbursable non-debit rules", () => {
        const dom = createDom(`
            <form
                data-rule-editor
                data-reimbursable-tag-name="Reimbursable"
                data-reimbursable-direction-error="Use debit for reimbursable rules."
            >
                <div class="alert d-none" data-rule-editor-error></div>
                <select name="direction">
                    <option value="any" selected>Any</option>
                    <option value="debit">Debit</option>
                </select>
                <input type="checkbox" name="tags" value="Reimbursable" checked>
            </form>
        `);
        const { document } = dom.window;

        loadScript(dom, "core.js");
        loadScript(dom, "rules.js");

        const form = document.querySelector("form");
        const invalidSubmit = new dom.window.Event("submit", { bubbles: true, cancelable: true });

        expect(form.dispatchEvent(invalidSubmit)).toBe(false);
        expect(invalidSubmit.defaultPrevented).toBe(true);
        expect(document.querySelector("[data-rule-editor-error]").textContent).toBe(
            "Use debit for reimbursable rules."
        );
        expect(document.querySelector("[data-rule-editor-error]").classList.contains("d-none")).toBe(false);
        expect(document.activeElement).toBe(document.querySelector("[name='direction']"));

        document.querySelector("[name='direction']").value = "debit";
        dispatch(dom.window, document.querySelector("[name='direction']"), "change");

        expect(document.querySelector("[data-rule-editor-error]").textContent).toBe("");
        expect(document.querySelector("[data-rule-editor-error]").classList.contains("d-none")).toBe(true);

        const validSubmit = new dom.window.Event("submit", { bubbles: true, cancelable: true });
        expect(form.dispatchEvent(validSubmit)).toBe(true);
        expect(validSubmit.defaultPrevented).toBe(false);

        dom.window.close();
    });
});
