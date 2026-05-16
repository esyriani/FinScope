const sidebarToggle = document.querySelector("[data-sidebar-toggle]");

function getCsrfToken() {
    return document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";
}

function setSidebarCollapsed(collapsed) {
    document.documentElement.classList.toggle("sidebar-collapsed", collapsed);
    localStorage.setItem("finance.sidebarCollapsed", String(collapsed));

    if (sidebarToggle) {
        sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
    }
}

if (sidebarToggle) {
    const initialState = document.documentElement.classList.contains("sidebar-collapsed");
    sidebarToggle.setAttribute("aria-expanded", String(!initialState));

    sidebarToggle.addEventListener("click", () => {
        const collapsed = !document.documentElement.classList.contains("sidebar-collapsed");
        setSidebarCollapsed(collapsed);
    });
}

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

setupTooltips();

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}
