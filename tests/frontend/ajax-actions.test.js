import { afterEach, describe, expect, it, vi } from "vitest";

import { createDom, loadScript } from "./support/dom.js";

describe("AJAX refresh runtime behavior", () => {
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it("replaces targets and runs registered initializers on the fresh DOM", async () => {
        const dom = createDom('<main id="target" data-ajax-refresh-target="main">Old content</main>');
        const { document } = dom.window;

        dom.window.fetch = vi.fn(async () => ({
            ok: true,
            url: "http://localhost/next?period=month",
            text: async () => `
                <!doctype html>
                <title>Updated section</title>
                <main id="target" data-ajax-refresh-target="main">
                    <button type="button" data-runtime-probe>Loaded</button>
                </main>
            `,
        }));

        loadScript(dom, "core.js");
        loadScript(dom, "ajax-actions.js");

        const initializedRoots = [];
        dom.window.financeApp.registerInitializer("runtime.probe", (root) => {
            initializedRoots.push(root.id);
            root.querySelector("[data-runtime-probe]")?.setAttribute("data-ready", "true");
        });

        const refreshEvents = [];
        document.addEventListener("finance:ajax-refreshed", (event) => refreshEvents.push(event.detail));

        const result = await dom.window.ajaxRefreshFromUrl("/next?period=month", "#target");

        expect(result).toMatchObject({ applied: true, selector: "#target", stale: false });
        expect(document.title).toBe("Updated section");
        expect(dom.window.location.pathname).toBe("/next");
        expect(dom.window.location.search).toBe("?period=month");
        expect(document.querySelector("[data-runtime-probe]")?.dataset.ready).toBe("true");
        expect(initializedRoots).toEqual(["target"]);
        expect(refreshEvents).toHaveLength(1);
        expect(refreshEvents[0].targets[0]).toBe(document.querySelector("#target"));

        dom.window.close();
    });

    it("ignores stale responses after a newer refresh wins the same target", async () => {
        const dom = createDom('<main id="target" data-ajax-refresh-target="main">Original</main>');
        const { document } = dom.window;
        const pendingResponses = [];
        const signals = [];

        dom.window.fetch = vi.fn((_url, options) => {
            signals.push(options.signal);
            return new Promise((resolve) => pendingResponses.push(resolve));
        });

        loadScript(dom, "core.js");
        loadScript(dom, "ajax-actions.js");

        const firstRefresh = dom.window.ajaxRefreshFromUrl("/slow", "#target");
        const secondRefresh = dom.window.ajaxRefreshFromUrl("/fast", "#target");

        expect(signals[0].aborted).toBe(true);
        expect(signals[1].aborted).toBe(false);

        pendingResponses[1]({
            ok: true,
            url: "http://localhost/fast",
            text: async () => '<main id="target" data-ajax-refresh-target="main">Fast</main>',
        });

        await expect(secondRefresh).resolves.toMatchObject({ applied: true, stale: false });
        expect(document.querySelector("#target").textContent).toBe("Fast");

        pendingResponses[0]({
            ok: true,
            url: "http://localhost/slow",
            text: async () => '<main id="target" data-ajax-refresh-target="main">Slow</main>',
        });

        await expect(firstRefresh).resolves.toMatchObject({ applied: false, stale: true });
        expect(document.querySelector("#target").textContent).toBe("Fast");

        dom.window.close();
    });
});
