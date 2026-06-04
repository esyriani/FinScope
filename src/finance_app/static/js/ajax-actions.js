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

function disposeAjaxRefreshTooltips(target) {
    if (!window.bootstrap?.Tooltip) {
        return;
    }

    target.querySelectorAll("[data-bs-tooltip]").forEach((element) => {
        window.bootstrap.Tooltip.getInstance(element)?.dispose();
    });
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

function hideAjaxRefreshModal(form) {
    const modal = form.closest(".modal");
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
        bootstrap.Modal.getOrCreateInstance(modal).hide();
        window.setTimeout(finish, 350);
    });
}

function updateAjaxRefreshUrl(url) {
    if (!url) {
        return;
    }

    const nextUrl = new URL(url, window.location.href);
    if (nextUrl.origin !== window.location.origin) {
        return;
    }

    window.history.replaceState(
        window.history.state,
        "",
        `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`
    );
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
        throw new Error("Refresh target was not found.");
    }

    const replacements = [];
    currentTargets.forEach((target, index) => {
        disposeAjaxRefreshTooltips(target);
        window.disposeDashboardCharts?.(target);
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
    document.dispatchEvent(new CustomEvent("finance:ajax-refreshed", {
        detail: { selector, targets: replacements },
    }));
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

async function ajaxRefreshFromUrl(url, selector) {
    const response = await fetch(url, {
        method: "GET",
        headers: {
            "X-Requested-With": "fetch",
        },
        credentials: "same-origin",
    });
    const html = await response.text();
    if (!response.ok) {
        throw new Error("The page section could not be refreshed.");
    }
    replaceAjaxRefreshTargets(selector, html, response.url);
}

async function handleAjaxRefreshResponse(response, form, selector) {
    const contentType = response.headers.get("content-type") || "";

    if (contentType.includes("application/json")) {
        const data = await response.json();
        if (!response.ok || data.ok === false) {
            throw new Error(data.message || "The action could not be completed.");
        }

        const refreshUrl = data.refresh_url || data.redirect_url || response.url || window.location.href;
        await hideAjaxRefreshModal(form);
        await ajaxRefreshFromUrl(refreshUrl, selector);
        return;
    }

    const html = await response.text();
    if (!response.ok) {
        throw new Error("The action could not be completed.");
    }

    await hideAjaxRefreshModal(form);
    replaceAjaxRefreshTargets(selector, html, response.url);
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

    const controls = Array.from(form.querySelectorAll("button, input, select, textarea"));
    controls.forEach((control) => {
        control.disabled = true;
    });
    form.setAttribute("aria-busy", "true");
    const method = (form.method || "POST").toUpperCase();
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
        } else {
            fetchOptions.body = formData;
        }

        const response = await fetch(actionUrl, fetchOptions);
        await handleAjaxRefreshResponse(response, form, selector);
    } catch (error) {
        showAjaxRefreshError(
            form,
            selector,
            error?.message || "The action could not be completed."
        );
    } finally {
        if (document.body.contains(form)) {
            controls.forEach((control) => {
                control.disabled = false;
            });
            form.removeAttribute("aria-busy");
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
                event.defaultPrevented
                || event.button !== 0
                || event.metaKey
                || event.ctrlKey
                || event.shiftKey
                || event.altKey
                || link.target
                || link.hasAttribute("download")
            ) {
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
            target?.setAttribute("aria-busy", "true");
            link.setAttribute("aria-disabled", "true");
            const busyToken = window.showBusyOverlayForElement?.(link);

            try {
                await ajaxRefreshFromUrl(url, selector);
            } catch (error) {
                showAjaxRefreshError(
                    target || link,
                    selector,
                    error?.message || "The page section could not be refreshed."
                );
            } finally {
                if (document.body.contains(link)) {
                    link.removeAttribute("aria-disabled");
                }
                if (target && document.body.contains(target)) {
                    target.removeAttribute("aria-busy");
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

setupAjaxRefreshForms();
setupAjaxRefreshLinks();
