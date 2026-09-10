const ajaxRefreshRequests = new Map();
let ajaxRefreshSequence = 0;

function ajaxRefreshTranslate(message, variables) {
    return window.financeTranslate ? window.financeTranslate(message, variables) : message;
}

function ajaxRefreshTargetSelector(element) {
    const explicitSelector = element.dataset.ajaxRefreshTarget;
    if (explicitSelector) {
        return explicitSelector;
    }

    const target = element.closest("[data-ajax-refresh-target]");
    if (!target) {
        return "";
    }

    if (target.id) {
        return `#${target.id}`;
    }

    const key = target.dataset.ajaxRefreshTarget || "";
    if (!key) {
        return "";
    }

    if (window.CSS?.escape) {
        return `[data-ajax-refresh-target="${CSS.escape(key)}"]`;
    }

    return `[data-ajax-refresh-target="${key.replaceAll('"', '\\"')}"]`;
}

function ajaxRefreshAbortController() {
    return typeof AbortController === "function" ? new AbortController() : null;
}

function beginAjaxRefreshRequest(selector, options = {}) {
    const previousRequest = ajaxRefreshRequests.get(selector);
    previousRequest?.controller?.abort();

    const request = {
        controller: options.abortable === false ? null : ajaxRefreshAbortController(),
        selector,
        sequence: (ajaxRefreshSequence += 1),
    };
    ajaxRefreshRequests.set(selector, request);
    return request;
}

function prepareAjaxRefreshRequestForGet(request) {
    if (!request.controller) {
        request.controller = ajaxRefreshAbortController();
    }
}

function ajaxRefreshIsCurrentRequest(request) {
    const currentRequest = ajaxRefreshRequests.get(request.selector);
    return currentRequest?.sequence === request.sequence;
}

function finishAjaxRefreshRequest(request) {
    if (ajaxRefreshIsCurrentRequest(request)) {
        ajaxRefreshRequests.delete(request.selector);
    }
}

function ajaxRefreshStaleResult(request) {
    return {
        applied: false,
        selector: request.selector,
        stale: true,
    };
}

function ajaxRefreshAppliedResult(request) {
    return {
        applied: true,
        selector: request.selector,
        stale: false,
    };
}

function ajaxRefreshTargetElements(selector) {
    if (!selector) {
        return [];
    }

    try {
        return Array.from(document.querySelectorAll(selector));
    } catch (_error) {
        return [];
    }
}

function setAjaxRefreshTargetsBusy(selector, busy) {
    ajaxRefreshTargetElements(selector).forEach((target) => {
        if (busy) {
            target.setAttribute("aria-busy", "true");
        } else {
            target.removeAttribute("aria-busy");
        }
    });
}

function ajaxRefreshTargetAndDescendants(target, selector) {
    const elements = [];
    if (target.matches(selector)) {
        elements.push(target);
    }
    elements.push(...target.querySelectorAll(selector));
    return elements;
}

function disposeAjaxRefreshTooltips(target) {
    if (!window.bootstrap?.Tooltip) {
        return;
    }

    ajaxRefreshTargetAndDescendants(target, "[data-bs-tooltip], [data-bs-original-title], [aria-describedby]").forEach(
        (element) => {
            window.bootstrap.Tooltip.getInstance(element)?.dispose();
        }
    );
}

function disposeAjaxRefreshTargetWidgets(target) {
    disposeAjaxRefreshTooltips(target);
    window.disposeDashboardCharts?.(target);
}

function cleanupAjaxRefreshModals() {
    if (document.querySelector(".modal.show")) {
        return;
    }

    document.querySelectorAll(".modal-backdrop").forEach((backdrop) => backdrop.remove());
    document.body.classList.remove("modal-open");
    document.body.style.removeProperty("overflow");
    document.body.style.removeProperty("padding-right");
}

function hideAjaxRefreshModalElement(modal) {
    if (!modal || !modal.classList.contains("show") || !window.bootstrap?.Modal) {
        cleanupAjaxRefreshModals();
        return Promise.resolve();
    }

    return new Promise((resolve) => {
        let resolved = false;
        const finish = () => {
            if (resolved) {
                return;
            }
            resolved = true;
            cleanupAjaxRefreshModals();
            resolve();
        };

        modal.addEventListener("hidden.bs.modal", finish, { once: true });
        window.bootstrap.Modal.getOrCreateInstance(modal).hide();
        window.setTimeout(finish, 350);
    });
}

function hideAjaxRefreshModal(form) {
    return hideAjaxRefreshModalElement(form.closest(".modal"));
}

async function hideAjaxRefreshTargetModals(target) {
    const openModals = ajaxRefreshTargetAndDescendants(target, ".modal.show");
    if (!openModals.length) {
        return;
    }

    await Promise.all(openModals.map((modal) => hideAjaxRefreshModalElement(modal)));
}

function updateAjaxRefreshUrl(url) {
    if (!url) {
        return;
    }

    const nextUrl = new URL(url, window.location.href);
    if (nextUrl.origin !== window.location.origin) {
        return;
    }

    window.history.replaceState(window.history.state, "", `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`);
}

function runAjaxRefreshInitializers(root = document) {
    setupAjaxRefreshForms(root);
    setupAjaxRefreshLinks(root);
    window.financeApp?.runInitializers(root);
}

function replaceAjaxRefreshTargets(selector, html, responseUrl) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const currentTargets = ajaxRefreshTargetElements(selector);
    const freshTargets = Array.from(doc.querySelectorAll(selector));

    if (!currentTargets.length || currentTargets.length !== freshTargets.length) {
        throw new Error(ajaxRefreshTranslate("Refresh target was not found."));
    }

    const replacements = [];
    currentTargets.forEach((target, index) => {
        disposeAjaxRefreshTargetWidgets(target);
        const replacement = document.importNode(freshTargets[index], true);
        target.replaceWith(replacement);
        replacements.push(replacement);
    });

    if (doc.title) {
        document.title = doc.title;
    }

    updateAjaxRefreshUrl(responseUrl);
    cleanupAjaxRefreshModals();
    replacements.forEach((replacement) => runAjaxRefreshInitializers(replacement));
    document.dispatchEvent(
        new CustomEvent("finance:ajax-refreshed", {
            detail: { selector, targets: replacements },
        })
    );
}

function showAjaxRefreshError(form, selector, message) {
    const target = form.closest("[data-ajax-refresh-target]") || ajaxRefreshTargetElements(selector)[0];
    if (!target) {
        return;
    }

    let alert = target.querySelector("[data-ajax-refresh-error]");
    if (!alert) {
        alert = document.createElement("div");
        alert.className = "alert alert-danger";
        alert.setAttribute("role", "alert");
        alert.setAttribute("data-ajax-refresh-error", "");
        target.prepend(alert);
    }

    alert.textContent = message;
}

async function ajaxRefreshFromUrl(url, selector, options = {}) {
    const request = options.request || beginAjaxRefreshRequest(selector);
    if (!ajaxRefreshIsCurrentRequest(request)) {
        return ajaxRefreshStaleResult(request);
    }

    prepareAjaxRefreshRequestForGet(request);
    setAjaxRefreshTargetsBusy(selector, true);

    try {
        const response = await fetch(url, {
            method: "GET",
            headers: {
                "X-Requested-With": "fetch",
            },
            credentials: "same-origin",
            signal: request.controller?.signal,
        });
        const html = await response.text();
        if (!response.ok) {
            throw new Error(ajaxRefreshTranslate("The page section could not be refreshed."));
        }
        if (!ajaxRefreshIsCurrentRequest(request)) {
            return ajaxRefreshStaleResult(request);
        }

        replaceAjaxRefreshTargets(selector, html, response.url);
        return ajaxRefreshAppliedResult(request);
    } catch (error) {
        if (error?.name === "AbortError" || !ajaxRefreshIsCurrentRequest(request)) {
            return ajaxRefreshStaleResult(request);
        }
        throw error;
    } finally {
        if (ajaxRefreshIsCurrentRequest(request)) {
            setAjaxRefreshTargetsBusy(selector, false);
            finishAjaxRefreshRequest(request);
        }
    }
}

function ajaxRefreshDynamicTarget(selector) {
    return ajaxRefreshTargetElements(selector)[0] || null;
}

function setAjaxRefreshDynamicBusy(selector, loadingClass, busy) {
    ajaxRefreshTargetElements(selector).forEach((target) => {
        target.setAttribute("aria-busy", busy ? "true" : "false");
        if (loadingClass) {
            target.classList.toggle(loadingClass, busy);
        }
    });
}

function ajaxRefreshDynamicBasePath(options) {
    const routeUrl = ajaxRefreshDynamicTarget(options.selector)?.dataset[options.routeDatasetKey] || "";
    return routeUrl ? new URL(routeUrl, window.location.href).pathname : "";
}

function ajaxRefreshDynamicUrl(value, options) {
    let url;
    try {
        url = new URL(value, window.location.href);
    } catch (_error) {
        return null;
    }

    const basePath = ajaxRefreshDynamicBasePath(options);
    return url.origin === window.location.origin && basePath && url.pathname === basePath ? url : null;
}

function ajaxRefreshFormUrl(form) {
    const url = new URL(form.getAttribute("action") || window.location.href, window.location.href);
    url.search = new URLSearchParams(new FormData(form)).toString();
    return url;
}

async function disposeAjaxRefreshDynamicTarget(currentTarget, context, options) {
    disposeAjaxRefreshTargetWidgets(currentTarget);
    await hideAjaxRefreshTargetModals(currentTarget);
    await options.disposeTarget?.(context);
    await options.beforeReplace?.(context);
}

async function ajaxRefreshDynamicPage(url, options, replaceOptions = {}) {
    const currentTarget = ajaxRefreshDynamicTarget(options.selector);
    if (!currentTarget) {
        window.location.href = url.toString();
        return { applied: false, redirected: true, selector: options.selector, stale: false };
    }

    const request = beginAjaxRefreshRequest(options.selector);
    setAjaxRefreshDynamicBusy(options.selector, options.loadingClass, true);

    try {
        const response = await fetch(url.toString(), {
            headers: { "X-Requested-With": options.requestedWith || "XMLHttpRequest" },
            signal: request.controller?.signal,
        });
        if (!response.ok) {
            throw new Error(ajaxRefreshTranslate(options.errorMessage || "Page refresh failed."));
        }

        const documentText = await response.text();
        const nextDocument = new DOMParser().parseFromString(documentText, "text/html");
        const nextTarget = nextDocument.querySelector(options.selector);
        if (!nextTarget) {
            throw new Error(ajaxRefreshTranslate(options.missingMessage || "Page refresh returned no content."));
        }
        if (!ajaxRefreshIsCurrentRequest(request)) {
            return ajaxRefreshStaleResult(request);
        }

        await disposeAjaxRefreshDynamicTarget(currentTarget, { currentTarget, nextDocument, nextTarget, url }, options);
        if (!ajaxRefreshIsCurrentRequest(request)) {
            return ajaxRefreshStaleResult(request);
        }

        const replacement = document.importNode(nextTarget, true);
        currentTarget.replaceWith(replacement);
        cleanupAjaxRefreshModals();
        if (replaceOptions.pushState !== false) {
            window.history.pushState(options.historyState || {}, "", url.toString());
        }
        options.afterReplace?.({ nextDocument, target: replacement, url });
        runAjaxRefreshInitializers(replacement);
        return ajaxRefreshAppliedResult(request);
    } catch (error) {
        if (error?.name === "AbortError" || !ajaxRefreshIsCurrentRequest(request)) {
            return ajaxRefreshStaleResult(request);
        }
        if (replaceOptions.fallback !== false) {
            window.location.href = url.toString();
            return { applied: false, redirected: true, selector: options.selector, stale: false };
        }
        throw error;
    } finally {
        if (ajaxRefreshIsCurrentRequest(request)) {
            setAjaxRefreshDynamicBusy(options.selector, options.loadingClass, false);
            finishAjaxRefreshRequest(request);
        }
    }
}

function createAjaxDynamicPageRefresh(options = {}) {
    return {
        element: () => ajaxRefreshDynamicTarget(options.selector),
        formUrl: ajaxRefreshFormUrl,
        replace: (url, replaceOptions = {}) => ajaxRefreshDynamicPage(url, options, replaceOptions),
        url: (value) => ajaxRefreshDynamicUrl(value, options),
    };
}

async function handleAjaxRefreshResponse(response, form, selector, request) {
    const contentType = response.headers.get("content-type") || "";

    if (contentType.includes("application/json")) {
        const data = await response.json();
        if (!response.ok || data.ok === false) {
            throw new Error(data.message || ajaxRefreshTranslate("The action could not be completed."));
        }

        const refreshUrl = data.refresh_url || data.redirect_url || response.url || window.location.href;
        await hideAjaxRefreshModal(form);
        const refreshResult = await ajaxRefreshFromUrl(refreshUrl, selector, { request });
        if (refreshResult.stale) {
            return refreshResult;
        }

        document.dispatchEvent(
            new CustomEvent("finance:ajax-action-complete", {
                detail: { form, selector, data, refreshUrl },
            })
        );
        return refreshResult;
    }

    const html = await response.text();
    if (!response.ok) {
        throw new Error(ajaxRefreshTranslate("The action could not be completed."));
    }

    await hideAjaxRefreshModal(form);
    if (!ajaxRefreshIsCurrentRequest(request)) {
        return ajaxRefreshStaleResult(request);
    }

    replaceAjaxRefreshTargets(selector, html, response.url);
    return ajaxRefreshAppliedResult(request);
}

async function submitAjaxRefreshForm(form, submitter) {
    const selector = ajaxRefreshTargetSelector(form);
    if (!selector) {
        form.submit();
        return;
    }

    if (!form.reportValidity()) {
        return;
    }

    const formData = new FormData(form);
    if (submitter?.name) {
        formData.append(submitter.name, submitter.value);
    }

    const method = (form.method || "POST").toUpperCase();
    const request = beginAjaxRefreshRequest(selector, { abortable: method === "GET" });
    const controls = Array.from(form.querySelectorAll("button, input, select, textarea"));
    controls.forEach((control) => {
        control.disabled = true;
    });
    form.setAttribute("aria-busy", "true");
    setAjaxRefreshTargetsBusy(selector, true);
    const busyToken = window.showBusyOverlayForElement?.(form, submitter);

    try {
        const actionUrl = new URL(form.action || window.location.href, window.location.href);
        const fetchOptions = {
            method,
            headers: {
                "X-Requested-With": "fetch",
                "X-CSRF-Token": getCsrfToken(),
            },
            credentials: "same-origin",
            redirect: "follow",
        };

        if (method === "GET") {
            actionUrl.search = new URLSearchParams(formData).toString();
            await ajaxRefreshFromUrl(actionUrl, selector, { request });
        } else {
            fetchOptions.body = formData;
            const response = await fetch(actionUrl, fetchOptions);
            await handleAjaxRefreshResponse(response, form, selector, request);
        }
    } catch (error) {
        if (ajaxRefreshIsCurrentRequest(request)) {
            showAjaxRefreshError(
                form,
                selector,
                error?.message || ajaxRefreshTranslate("The action could not be completed.")
            );
        }
    } finally {
        if (document.body.contains(form)) {
            controls.forEach((control) => {
                control.disabled = false;
            });
            form.removeAttribute("aria-busy");
        }
        if (ajaxRefreshIsCurrentRequest(request)) {
            setAjaxRefreshTargetsBusy(selector, false);
            finishAjaxRefreshRequest(request);
        }
        window.hideBusyOverlay?.(busyToken);
    }
}

function setupAjaxRefreshLinks(root = document) {
    const links = Array.from(root.querySelectorAll("a[data-ajax-refresh-link]"));

    links.forEach((link) => {
        if (link.dataset.ajaxRefreshLinkReady === "true") {
            return;
        }

        link.dataset.ajaxRefreshLinkReady = "true";
        link.addEventListener("click", async (event) => {
            if (
                event.defaultPrevented ||
                event.button !== 0 ||
                event.metaKey ||
                event.ctrlKey ||
                event.shiftKey ||
                event.altKey ||
                link.target ||
                link.hasAttribute("download")
            ) {
                return;
            }

            if (link.dataset.ajaxRefreshInFlight === "true") {
                event.preventDefault();
                return;
            }

            const selector = link.dataset.ajaxRefreshTarget || ajaxRefreshTargetSelector(link);
            if (!selector) {
                return;
            }

            const url = new URL(link.href, window.location.href);
            if (url.origin !== window.location.origin) {
                return;
            }

            event.preventDefault();
            const target = link.closest("[data-ajax-refresh-target]") || ajaxRefreshTargetElements(selector)[0];
            link.dataset.ajaxRefreshInFlight = "true";
            link.setAttribute("aria-disabled", "true");
            const busyToken = window.showBusyOverlayForElement?.(link);

            try {
                await ajaxRefreshFromUrl(url, selector);
            } catch (error) {
                showAjaxRefreshError(
                    target || link,
                    selector,
                    error?.message || ajaxRefreshTranslate("The page section could not be refreshed.")
                );
            } finally {
                if (document.body.contains(link)) {
                    delete link.dataset.ajaxRefreshInFlight;
                    link.removeAttribute("aria-disabled");
                }
                window.hideBusyOverlay?.(busyToken);
            }
        });
    });
}

function setupAjaxRefreshForms(root = document) {
    const forms = Array.from(root.querySelectorAll("[data-ajax-refresh-form]"));

    forms.forEach((form) => {
        if (form.dataset.ajaxRefreshReady === "true") {
            return;
        }

        form.dataset.ajaxRefreshReady = "true";
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            submitAjaxRefreshForm(form, event.submitter);
        });
    });
}

window.financeApp = {
    ...(window.financeApp || {}),
    createDynamicPageRefresh: createAjaxDynamicPageRefresh,
};

setupAjaxRefreshForms();
setupAjaxRefreshLinks();
