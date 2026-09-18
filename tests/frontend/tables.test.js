import { describe, expect, it, vi } from "vitest";

import { createDom, dispatch, loadScript } from "./support/dom.js";

function transactionTable() {
    return `
        <input data-table-search data-table-search-target="#transactions-table">
        <div class="table-responsive">
            <table id="transactions-table" data-sortable-table data-paginated-table data-page-size="2">
                <thead>
                    <tr>
                        <th><button type="button" data-sort-column="0" data-sort-type="text">Merchant</button></th>
                        <th><button type="button" data-sort-column="1" data-sort-type="number">Amount</button></th>
                    </tr>
                </thead>
                <tbody>
                    <tr><td>Metro</td><td data-sort-value="30">30.00</td></tr>
                    <tr><td>Bakery</td><td data-sort-value="10">10.00</td></tr>
                    <tr><td>Pharmacy</td><td data-sort-value="20">20.00</td></tr>
                </tbody>
            </table>
        </div>
    `;
}

describe("table runtime behavior", () => {
    it("sorts numeric columns and updates accessible sort state", () => {
        const dom = createDom(transactionTable());
        const { document } = dom.window;

        loadScript(dom, "tables.js");

        document.querySelector('[data-sort-column="1"]').click();

        const rows = [...document.querySelectorAll("tbody tr")];
        expect(rows.map((row) => row.cells[0].textContent)).toEqual(["Bakery", "Pharmacy", "Metro"]);
        expect(document.querySelector("thead th:nth-child(2)").getAttribute("aria-sort")).toBe("ascending");
        expect(document.querySelector("thead th:nth-child(2) .sort-icon.asc")).not.toBeNull();

        dom.window.close();
    });

    it("filters paginated rows and rerenders pagination from visible matches", () => {
        const dom = createDom(transactionTable());
        const { document } = dom.window;

        loadScript(dom, "tables.js");

        const rows = [...document.querySelectorAll("tbody tr")];
        expect(rows.map((row) => row.hidden)).toEqual([false, false, true]);
        expect(document.querySelector(".table-pagination-header").hidden).toBe(false);

        const search = document.querySelector("[data-table-search]");
        search.value = "pharmacy";
        dispatch(dom.window, search, "input");

        expect(rows.map((row) => row.hidden)).toEqual([true, true, false]);
        expect(rows[0].dataset.tableFilteredOut).toBe("true");
        expect(rows[1].dataset.tableFilteredOut).toBe("true");
        expect(rows[2].dataset.tableFilteredOut).toBeUndefined();
        expect(document.querySelector(".table-pagination-header").hidden).toBe(true);

        dom.window.close();
    });

    it("exposes row keyboard activation for modal-backed rows", () => {
        const dom = createDom(`
            <table>
                <tbody>
                    <tr data-row-edit-target="#edit-modal">
                        <td>Metro</td>
                        <td>12.34</td>
                    </tr>
                </tbody>
            </table>
            <div id="edit-modal"></div>
        `);
        const { document } = dom.window;
        const showModal = vi.fn();

        dom.window.bootstrap = {
            Modal: {
                getOrCreateInstance: vi.fn(() => ({ show: showModal })),
            },
        };

        loadScript(dom, "tables.js");

        const row = document.querySelector("tbody tr");
        expect(row.tabIndex).toBe(0);
        expect(row.getAttribute("role")).toBe("button");
        expect(row.getAttribute("aria-label")).toBe("Metro 12.34");

        row.dispatchEvent(new dom.window.KeyboardEvent("keydown", { bubbles: true, cancelable: true, key: "Enter" }));

        expect(showModal).toHaveBeenCalledWith(row);

        dom.window.close();
    });
});
