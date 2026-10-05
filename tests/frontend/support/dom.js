import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { JSDOM } from "jsdom";
import { vi } from "vitest";

const PROJECT_ROOT = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const STATIC_JS = path.join(PROJECT_ROOT, "src", "finance_app", "static", "js");

function interpolate(message, variables = {}) {
    return Object.entries(variables || {}).reduce(
        (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)),
        message
    );
}

function matchMediaStub() {
    return {
        matches: false,
        media: "",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
    };
}

function cssEscape(value) {
    return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

export function createDom(body = "", options = {}) {
    const dom = new JSDOM(
        `<!doctype html>
        <html>
            <head>
                <title>${options.title || "FinScope"}</title>
                <meta name="csrf-token" content="${options.csrfToken || "test-csrf"}">
            </head>
            <body>${body}</body>
        </html>`,
        {
            pretendToBeVisual: true,
            runScripts: "outside-only",
            url: options.url || "http://localhost/current",
        }
    );
    const { window } = dom;

    window.matchMedia = window.matchMedia || matchMediaStub;
    window.CSS = window.CSS || {};
    window.CSS.escape = window.CSS.escape || cssEscape;
    window.financeTranslate = options.translate || interpolate;
    window.alert = vi.fn();
    window.showBusyOverlay = vi.fn(() => "busy-token");
    window.hideBusyOverlay = vi.fn();
    window.showBusyOverlayForElement = vi.fn(() => "element-busy-token");

    Object.defineProperty(window.HTMLFormElement.prototype, "reportValidity", {
        configurable: true,
        value: vi.fn(() => true),
    });

    return dom;
}

export function loadScript(dom, scriptName) {
    const source = readFileSync(path.join(STATIC_JS, scriptName), "utf8");
    dom.window.eval(`${source}\n//# sourceURL=${scriptName}`);
}

export function dispatch(window, element, type, options = {}) {
    element.dispatchEvent(
        new window.Event(type, {
            bubbles: true,
            cancelable: true,
            ...options,
        })
    );
}

export async function flushAsync() {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await Promise.resolve();
}
