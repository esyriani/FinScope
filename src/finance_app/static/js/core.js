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

window.financeApp.registerInitializer("core.tooltips", setupTooltips);
window.financeApp.registerInitializer("core.auto-show-modals", setupAutoShowModals);

setupTooltips();
setupAutoShowModals();

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}
