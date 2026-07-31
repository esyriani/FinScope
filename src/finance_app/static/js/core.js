const sidebarToggles = Array.from(document.querySelectorAll("[data-sidebar-toggle], [data-sidebar-mobile-toggle]"));
const sidebarBackdrop = document.querySelector("[data-sidebar-backdrop]");
const sidebarMediaQuery = window.matchMedia("(max-width: 850px)");
const financeInitializers = new Map();

function registerFinanceInitializer(name, initializer) {
    if (!name || typeof initializer !== "function") {
        return;
    }

    financeInitializers.set(name, initializer);
}

function runFinanceInitializers(root = document) {
    financeInitializers.forEach((initializer) => initializer(root));
}

window.financeApp = {
    ...(window.financeApp || {}),
    registerInitializer: registerFinanceInitializer,
    runInitializers: runFinanceInitializers,
};

function getCsrfToken() {
    return document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";
}

function notifyLayoutChanged() {
    window.dispatchEvent(new CustomEvent("finance:layoutchange"));
}

function isMobileSidebar() {
    return sidebarMediaQuery.matches;
}

function updateSidebarToggleState() {
    const mobileOpen = document.documentElement.classList.contains("sidebar-open");
    const desktopCollapsed = document.documentElement.classList.contains("sidebar-collapsed");
    const expanded = isMobileSidebar() ? mobileOpen : !desktopCollapsed;

    sidebarToggles.forEach((toggle) => {
        toggle.setAttribute("aria-expanded", String(expanded));
    });
}

function setSidebarOpen(open) {
    document.documentElement.classList.toggle("sidebar-open", open);
    updateSidebarToggleState();
    notifyLayoutChanged();
}

function setSidebarCollapsed(collapsed) {
    document.documentElement.classList.toggle("sidebar-collapsed", collapsed);
    localStorage.setItem("finance.sidebarCollapsed", String(collapsed));
    updateSidebarToggleState();
    notifyLayoutChanged();
}

if (sidebarToggles.length) {
    updateSidebarToggleState();

    sidebarToggles.forEach((toggle) => {
        toggle.addEventListener("click", () => {
            if (isMobileSidebar()) {
                setSidebarOpen(!document.documentElement.classList.contains("sidebar-open"));
                return;
            }

            const collapsed = !document.documentElement.classList.contains("sidebar-collapsed");
            setSidebarCollapsed(collapsed);
        });
    });
}

sidebarBackdrop?.addEventListener("click", () => setSidebarOpen(false));

document.querySelectorAll(".sidebar a").forEach((link) => {
    link.addEventListener("click", () => {
        if (isMobileSidebar()) {
            setSidebarOpen(false);
        }
    });
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isMobileSidebar()) {
        setSidebarOpen(false);
    }
});

sidebarMediaQuery.addEventListener("change", () => {
    setSidebarOpen(false);
    updateSidebarToggleState();
    notifyLayoutChanged();
});

const themeModeInputs = Array.from(document.querySelectorAll("input[name='theme_mode']"));
themeModeInputs.forEach((input) => {
    input.addEventListener("change", () => {
        if (input.checked) {
            document.documentElement.setAttribute("data-bs-theme", input.value === "dark" ? "dark" : "light");
        }
    });
});

function setupBootstrapTooltips(root = document) {
    if (!window.bootstrap?.Tooltip) {
        return;
    }

    root.querySelectorAll("[data-bs-tooltip]").forEach((element) => {
        window.bootstrap.Tooltip.getOrCreateInstance(element, {
            container: "body",
            trigger: "hover focus",
        });
    });
}

function categoryDescriptionForSelect(select) {
    const option = select.options[select.selectedIndex];
    return option?.dataset.categoryDescription || option?.getAttribute("title") || "";
}

function updateCategoryDescriptionTooltip(select) {
    const description = categoryDescriptionForSelect(select);
    select.setAttribute("title", description);

    if (!window.bootstrap?.Tooltip) {
        return;
    }

    const tooltip = window.bootstrap.Tooltip.getInstance(select);
    if (!description) {
        tooltip?.dispose();
        return;
    }

    if (tooltip) {
        select.setAttribute("data-bs-original-title", description);
        tooltip.setContent({ ".tooltip-inner": description });
    } else {
        window.bootstrap.Tooltip.getOrCreateInstance(select, {
            container: "body",
            trigger: "hover focus",
        });
    }
}

function setupCategoryDescriptionSelects(root = document) {
    root.querySelectorAll("[data-category-description-select]").forEach((select) => {
        updateCategoryDescriptionTooltip(select);
        if (select.dataset.categoryDescriptionReady === "true") {
            return;
        }

        select.dataset.categoryDescriptionReady = "true";
        ["change", "focus", "mouseenter"].forEach((eventName) => {
            select.addEventListener(eventName, () => updateCategoryDescriptionTooltip(select));
        });
    });
}

function setupTooltips(root = document) {
    setupCategoryDescriptionSelects(root);
    setupBootstrapTooltips(root);
}

function setupAutoShowModals(root = document) {
    if (!window.bootstrap?.Modal) {
        return;
    }

    root.querySelectorAll(".modal[data-auto-show-modal]").forEach((modal) => {
        if (modal.dataset.autoShowModalReady === "true") {
            return;
        }
        modal.dataset.autoShowModalReady = "true";
        window.bootstrap.Modal.getOrCreateInstance(modal).show();
    });
}

const financeCollapsePanelHeaderInteractiveSelector = [
    "a",
    "button",
    "input",
    "select",
    "textarea",
    "label",
    "summary",
    "[role='button']",
    "[data-bs-toggle]",
].join(", ");

const financeCollapsePanelHeadingSelector = "[data-filter-panel-heading-toggle], [data-collapse-panel-heading-toggle]";
const financeCollapsePanelHeaderSelector = "[data-filter-panel-header-toggle], [data-collapse-panel-header-toggle]";

function financeCollapseTargetForToggle(toggle) {
    const targetSelector =
        toggle.dataset.collapsePanelTarget ||
        toggle.dataset.filterPanelTarget ||
        toggle.getAttribute("data-bs-target") ||
        "";
    return targetSelector ? document.querySelector(targetSelector) : null;
}

function financeCollapsePanelHeadingToggles(target) {
    if (!target?.id) return [];
    return Array.from(document.querySelectorAll(financeCollapsePanelHeadingSelector)).filter(
        (heading) => heading.getAttribute("aria-controls") === target.id
    );
}

function financeSetCollapsePanelHeadingExpanded(target, expanded) {
    financeCollapsePanelHeadingToggles(target).forEach((heading) => {
        heading.setAttribute("aria-expanded", expanded ? "true" : "false");
    });
}

function financeToggleCollapsePanelTarget(target) {
    if (!target) return;

    if (window.bootstrap?.Collapse) {
        window.bootstrap.Collapse.getOrCreateInstance(target, { toggle: false }).toggle();
        return;
    }

    target.classList.toggle("show");
    financeSetCollapsePanelHeadingExpanded(target, target.classList.contains("show"));
}

function setupCoreCollapsePanelStateSync(target) {
    if (!target || target.dataset.collapsePanelStateReady === "true") {
        return;
    }

    target.dataset.collapsePanelStateReady = "true";
    financeSetCollapsePanelHeadingExpanded(target, target.classList.contains("show"));
    target.addEventListener("shown.bs.collapse", () => financeSetCollapsePanelHeadingExpanded(target, true));
    target.addEventListener("hidden.bs.collapse", () => financeSetCollapsePanelHeadingExpanded(target, false));
}

function setupCoreFilterPanelHeaderToggles(root = document) {
    root.querySelectorAll(financeCollapsePanelHeaderSelector).forEach((header) => {
        if (header.dataset.collapsePanelHeaderReady === "true") {
            return;
        }

        const target = financeCollapseTargetForToggle(header);
        if (!target) return;
        setupCoreCollapsePanelStateSync(target);

        header.dataset.collapsePanelHeaderReady = "true";
        header.addEventListener("click", (event) => {
            const headingToggle = event.target.closest(financeCollapsePanelHeadingSelector);
            const interactiveElement = event.target.closest(financeCollapsePanelHeaderInteractiveSelector);
            if (interactiveElement && !headingToggle?.contains(interactiveElement)) {
                return;
            }

            financeToggleCollapsePanelTarget(target);
        });
    });

    root.querySelectorAll(financeCollapsePanelHeadingSelector).forEach((heading) => {
        if (heading.dataset.collapsePanelHeadingReady === "true") {
            return;
        }

        const target = financeCollapseTargetForToggle(heading);
        if (!target) return;
        setupCoreCollapsePanelStateSync(target);

        heading.dataset.collapsePanelHeadingReady = "true";
        heading.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" && event.key !== " ") {
                return;
            }

            event.preventDefault();
            financeToggleCollapsePanelTarget(target);
        });
    });
}

function setupCoreCollapseToggleLabels(root = document) {
    root.querySelectorAll("[data-collapse-label-toggle]").forEach((button) => {
        if (button.dataset.collapseLabelReady === "true") {
            return;
        }

        button.dataset.collapseLabelReady = "true";
        const targetSelector = button.getAttribute("data-bs-target") || button.getAttribute("href");
        const target = targetSelector ? document.querySelector(targetSelector) : null;
        const icon = button.querySelector("[data-collapse-toggle-icon]");
        const label = button.querySelector("[data-collapse-toggle-label]");
        const showLabel = button.dataset.showLabel || "Show";
        const hideLabel = button.dataset.hideLabel || "Hide";

        if (!target || !label) {
            return;
        }

        target.addEventListener("show.bs.collapse", () => {
            label.textContent = hideLabel;
            icon?.classList.replace("bi-chevron-down", "bi-chevron-up");
        });
        target.addEventListener("hide.bs.collapse", () => {
            label.textContent = showLabel;
            icon?.classList.replace("bi-chevron-up", "bi-chevron-down");
        });
    });
}

window.financeApp.registerInitializer("core.tooltips", setupTooltips);
window.financeApp.registerInitializer("core.auto-show-modals", setupAutoShowModals);
window.financeApp.registerInitializer("core.filter-panel-header-toggles", setupCoreFilterPanelHeaderToggles);
window.financeApp.registerInitializer("core.collapse-toggle-labels", setupCoreCollapseToggleLabels);

setupTooltips();
setupAutoShowModals();
setupCoreFilterPanelHeaderToggles();
setupCoreCollapseToggleLabels();

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}
