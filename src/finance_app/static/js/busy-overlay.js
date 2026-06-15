const busyOverlayState = {
    activeTokens: new Set(),
    visible: false,
    showTimer: null,
    previousActiveElement: null,
};

function busyOverlayRoot() {
    return document.querySelector("[data-busy-overlay-root]");
}

function busyOverlayMessageElement() {
    return busyOverlayRoot()?.querySelector("[data-busy-overlay-message]");
}

function busyOverlayTranslate(message) {
    return window.financeTranslate ? window.financeTranslate(message) : message;
}

function busyOverlayWantsOverlay(element, submitter = null) {
    return Boolean(submitter?.hasAttribute?.("data-busy-overlay") || element?.hasAttribute?.("data-busy-overlay"));
}

function busyOverlayOption(element, submitter, name, fallback) {
    const submitterValue = submitter?.dataset?.[name];
    if (submitterValue !== undefined && submitterValue !== "") {
        return submitterValue;
    }

    const elementValue = element?.dataset?.[name];
    if (elementValue !== undefined && elementValue !== "") {
        return elementValue;
    }

    return fallback;
}

function busyOverlayOptions(element, submitter = null) {
    const message = busyOverlayOption(element, submitter, "busyMessage", busyOverlayTranslate("Processing..."));
    const delayText = busyOverlayOption(element, submitter, "busyDelayMs", "300");
    const delayMs = Math.max(0, Number.parseInt(delayText, 10) || 0);

    return {
        delayMs,
        message,
    };
}

function renderBusyOverlay(token) {
    const overlay = busyOverlayRoot();
    if (!overlay || busyOverlayState.visible) {
        return;
    }

    const messageElement = busyOverlayMessageElement();
    if (messageElement) {
        messageElement.textContent = token.message || busyOverlayTranslate("Processing...");
    }

    busyOverlayState.previousActiveElement = document.activeElement;
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    document.body.classList.add("app-busy-active");
    document.body.setAttribute("aria-busy", "true");
    overlay.focus({ preventScroll: true });
    busyOverlayState.visible = true;
}

function hideRenderedBusyOverlay() {
    const overlay = busyOverlayRoot();
    if (!overlay) {
        return;
    }

    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    document.body.classList.remove("app-busy-active");
    document.body.removeAttribute("aria-busy");
    busyOverlayState.visible = false;

    const previous = busyOverlayState.previousActiveElement;
    if (previous && document.body.contains(previous) && typeof previous.focus === "function") {
        previous.focus({ preventScroll: true });
    }
    busyOverlayState.previousActiveElement = null;
}

function clearBusyOverlayTimer() {
    if (!busyOverlayState.showTimer) {
        return;
    }

    window.clearTimeout(busyOverlayState.showTimer);
    busyOverlayState.showTimer = null;
}

function showBusyOverlay(options = {}) {
    const token = {
        message: options.message || busyOverlayTranslate("Processing..."),
    };
    busyOverlayState.activeTokens.add(token);

    if (busyOverlayState.visible) {
        const messageElement = busyOverlayMessageElement();
        if (messageElement) {
            messageElement.textContent = token.message;
        }
        return token;
    }

    if (options.immediate === true) {
        renderBusyOverlay(token);
        return token;
    }

    clearBusyOverlayTimer();
    busyOverlayState.showTimer = window.setTimeout(
        () => {
            busyOverlayState.showTimer = null;
            const activeTokens = Array.from(busyOverlayState.activeTokens);
            const activeToken = activeTokens[activeTokens.length - 1];
            if (activeToken) {
                renderBusyOverlay(activeToken);
            }
        },
        Math.max(0, options.delayMs || 0)
    );

    return token;
}

function hideBusyOverlay(token) {
    if (!token) {
        return;
    }

    busyOverlayState.activeTokens.delete(token);
    if (busyOverlayState.activeTokens.size > 0) {
        return;
    }

    clearBusyOverlayTimer();
    if (busyOverlayState.visible) {
        hideRenderedBusyOverlay();
    }
}

function resetBusyOverlay() {
    busyOverlayState.activeTokens.clear();
    clearBusyOverlayTimer();
    hideRenderedBusyOverlay();
    document.querySelectorAll("form[data-busy-overlay-submitting='true']").forEach((form) => {
        delete form.dataset.busyOverlaySubmitting;
    });
}

function showBusyOverlayForElement(element, submitter = null) {
    if (!busyOverlayWantsOverlay(element, submitter)) {
        return null;
    }

    return showBusyOverlay(busyOverlayOptions(element, submitter));
}

function setupBusyOverlayNavigation() {
    if (window.busyOverlayNavigationReady === true) {
        return;
    }

    window.busyOverlayNavigationReady = true;
    document.addEventListener("submit", (event) => {
        if (event.defaultPrevented) {
            return;
        }

        const form = event.target?.closest?.("form");
        if (!form || form.hasAttribute("data-ajax-refresh-form")) {
            return;
        }

        if (!busyOverlayWantsOverlay(form, event.submitter)) {
            return;
        }

        if (!form.reportValidity()) {
            return;
        }

        if (form.dataset.busyOverlaySubmitting === "true") {
            event.preventDefault();
            return;
        }

        form.dataset.busyOverlaySubmitting = "true";
        showBusyOverlayForElement(form, event.submitter);
    });

    document.addEventListener("click", (event) => {
        const link = event.target?.closest?.("a[data-busy-overlay]");
        if (
            !link ||
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

        const url = new URL(link.href, window.location.href);
        if (url.origin !== window.location.origin) {
            return;
        }

        showBusyOverlayForElement(link);
    });
}

window.showBusyOverlay = showBusyOverlay;
window.hideBusyOverlay = hideBusyOverlay;
window.showBusyOverlayForElement = showBusyOverlayForElement;
window.resetBusyOverlay = resetBusyOverlay;

setupBusyOverlayNavigation();
window.addEventListener("pageshow", resetBusyOverlay);
