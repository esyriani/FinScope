import { afterEach, describe, expect, it, vi } from "vitest";

import { createDom, dispatch, flushAsync, loadScript } from "./support/dom.js";

function uploadPreviewDom() {
    return `
        <form data-upload-form data-preview-url="/upload/preview">
            <input name="date_order" data-upload-date-order value="auto">
            <button type="submit" data-upload-submit>Preview</button>
        </form>
        <div id="upload-preview-modal">
            <div data-upload-preview-error class="d-none"></div>
            <span data-upload-preview-count></span>
            <span data-upload-preview-ignored></span>
            <span data-upload-preview-date-range></span>
            <span data-upload-preview-date-format></span>
            <div data-upload-preview-date-choice class="d-none">
                <label><input type="radio" name="date_choice" value="month_first" data-upload-preview-date-option> Month</label>
                <label><input type="radio" name="date_choice" value="day_first" data-upload-preview-date-option> Day</label>
            </div>
            <div data-upload-preview-date-message></div>
            <table>
                <tbody data-upload-preview-rows></tbody>
            </table>
            <p data-upload-preview-empty class="d-none"></p>
            <button type="button" data-upload-preview-confirm>Import</button>
        </div>
    `;
}

describe("upload preview runtime behavior", () => {
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it("renders preview rows, date-format choices, and confirmed upload state", async () => {
        const dom = createDom(uploadPreviewDom());
        const { document } = dom.window;
        const modalShow = vi.fn();
        const modalHide = vi.fn();

        dom.window.bootstrap = {
            Modal: {
                getOrCreateInstance: vi.fn(() => ({ hide: modalHide, show: modalShow })),
            },
        };
        dom.window.fetch = vi.fn(async () => ({
            ok: true,
            json: async () => ({
                ok: true,
                preview: {
                    transaction_count: 1,
                    ignored_rows: 2,
                    date_format: {
                        effective_order: "",
                        has_date_order_dates: true,
                        has_slash_dates: true,
                        options: [
                            { value: "month_first", label: "MM/DD/YYYY" },
                            { value: "day_first", label: "DD/MM/YYYY" },
                        ],
                        requires_choice: true,
                        source: "ambiguous",
                    },
                    date_ranges: {
                        month_first: { earliest: "2026-02-03", latest: "2026-02-03" },
                        day_first: { earliest: "2026-03-02", latest: "2026-03-02" },
                    },
                    preview_rows: [
                        {
                            amount: "12.34",
                            day_first_date: "2026-03-02",
                            description: "Metro <Grocery>",
                            month_first_date: "2026-02-03",
                            parsed_date: "",
                            raw_date: "02/03/2026",
                        },
                    ],
                },
            }),
        }));

        loadScript(dom, "core.js");
        loadScript(dom, "upload.js");

        const form = document.querySelector("[data-upload-form]");
        form.requestSubmit = vi.fn();
        dispatch(dom.window, form, "submit");
        expect(dom.window.showBusyOverlay).toHaveBeenCalledWith(
            expect.objectContaining({
                message: "Preparing statement preview...",
            })
        );
        await flushAsync();

        expect(dom.window.fetch).toHaveBeenCalledWith(
            "/upload/preview",
            expect.objectContaining({
                credentials: "same-origin",
                headers: expect.objectContaining({
                    "X-CSRF-Token": "test-csrf",
                    "X-Requested-With": "fetch",
                }),
                method: "POST",
            })
        );
        expect(document.querySelector("[data-upload-preview-count]").textContent).toBe("1");
        expect(document.querySelector("[data-upload-preview-ignored]").textContent).toBe("2");
        expect(document.querySelector("[data-upload-preview-date-range]").textContent).toBe("Choose date format");
        expect(document.querySelector("[data-upload-preview-date-message]").textContent).toBe(
            "Choose the date format before importing."
        );
        expect(document.querySelector("[data-upload-preview-confirm]").disabled).toBe(true);
        expect(document.querySelector("[data-upload-preview-date-choice]").classList.contains("d-none")).toBe(false);
        expect([...document.querySelectorAll("[data-upload-preview-rows] td")].map((cell) => cell.textContent)).toEqual(
            ["02/03/2026", "Choose date format", "Metro <Grocery>", "12.34"]
        );
        expect(modalShow).toHaveBeenCalledOnce();
        expect(dom.window.hideBusyOverlay).toHaveBeenCalledWith("busy-token");

        const dayFirst = document.querySelector('input[value="day_first"]');
        dayFirst.checked = true;
        dispatch(dom.window, dayFirst, "change");

        expect(document.querySelector("[data-upload-date-order]").value).toBe("day_first");
        expect(document.querySelector("[data-upload-preview-parsed-date]").textContent).toBe("2026-03-02");
        expect(document.querySelector("[data-upload-preview-date-range]").textContent).toBe("2026-03-02");
        expect(document.querySelector("[data-upload-preview-date-format]").textContent).toBe("DD/MM/YYYY");
        expect(document.querySelector("[data-upload-preview-confirm]").disabled).toBe(false);

        document.querySelector("[data-upload-preview-confirm]").click();

        expect(modalHide).toHaveBeenCalledOnce();
        expect(form.requestSubmit).toHaveBeenCalledOnce();
        expect(form.dataset.uploadPreviewConfirmed).toBe("true");

        dom.window.close();
    });
});
