function renderTags(multiselect) {
    const tagsContainer = multiselect.querySelector("[data-tag-multiselect-tags]");
    const checkedInputs = Array.from(multiselect.querySelectorAll("input[type='checkbox']:checked"));
    const placeholder = multiselect.dataset.placeholder || "Select";

    if (!tagsContainer) return;

    tagsContainer.innerHTML = "";

    if (checkedInputs.length === 0) {
        const placeholderSpan = document.createElement("span");
        placeholderSpan.className = "text-muted";
        placeholderSpan.textContent = placeholder;
        tagsContainer.appendChild(placeholderSpan);
    } else {
        checkedInputs.forEach((input) => {
            const label = input.nextElementSibling;
            const labelText = label ? label.textContent.trim() : input.value;
            
            const tag = document.createElement("span");
            tag.className = "tag-multiselect-tag";
            
            const textSpan = document.createElement("span");
            textSpan.textContent = labelText;
            
            const closeBtn = document.createElement("button");
            closeBtn.type = "button";
            closeBtn.className = "tag-multiselect-remove";
            closeBtn.setAttribute("aria-label", financeTranslate("Remove {label}", { label: labelText }));
            closeBtn.textContent = "x";
            closeBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                input.checked = false;
                renderTags(multiselect);
            });
            
            tag.appendChild(textSpan);
            tag.appendChild(closeBtn);
            tagsContainer.appendChild(tag);
        });
    }
}


function positionMenu(multiselect) {
    const toggle = multiselect.querySelector("[data-tag-multiselect-toggle]");
    const menu = multiselect.querySelector("[data-tag-multiselect-menu]");
    
    if (!toggle || !menu) return;

    const rect = toggle.getBoundingClientRect();
    menu.style.top = (rect.bottom + window.scrollY) + "px";
    menu.style.left = rect.left + "px";
    menu.style.width = rect.width + "px";
}

function showMenu(multiselect) {
    const menu = multiselect.querySelector("[data-tag-multiselect-menu]");
    if (menu) {
        menu.style.display = "block";
        positionMenu(multiselect);
    }
}

function hideMenu(multiselect) {
    const menu = multiselect.querySelector("[data-tag-multiselect-menu]");
    if (menu) {
        menu.style.display = "none";
    }
}

function updateMenuPosition() {
    document.querySelectorAll("[data-tag-multiselect]").forEach(multiselect => {
        const menu = multiselect.querySelector("[data-tag-multiselect-menu]");
        if (menu && menu.style.display === "block") {
            positionMenu(multiselect);
        }
    });
}

function setupTagMultiselects(root = document) {
    const multiselects = Array.from(root.querySelectorAll("[data-tag-multiselect]"));

    multiselects.forEach((multiselect) => {
        if (multiselect.dataset.tagMultiselectReady === "true") {
            renderTags(multiselect);
            return;
        }

        multiselect.dataset.tagMultiselectReady = "true";
        const toggle = multiselect.querySelector("[data-tag-multiselect-toggle]");
        const inputs = Array.from(multiselect.querySelectorAll("input[type='checkbox']"));

        renderTags(multiselect);

        if (toggle) {
            toggle.addEventListener("click", (e) => {
                e.preventDefault();
                if (multiselect.dataset.disabled === "true") {
                    return;
                }

                document.querySelectorAll("[data-tag-multiselect]").forEach((other) => {
                    if (other !== multiselect) {
                        hideMenu(other);
                    }
                });

                const menu = multiselect.querySelector("[data-tag-multiselect-menu]");
                if (menu && menu.style.display === "block") {
                    hideMenu(multiselect);
                    toggle.setAttribute("aria-expanded", "false");
                } else {
                    showMenu(multiselect);
                    toggle.setAttribute("aria-expanded", "true");
                }
            });

            toggle.addEventListener("keydown", (event) => {
                if (multiselect.dataset.disabled === "true") {
                    return;
                }

                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    toggle.click();
                }

                if (event.key === "Escape") {
                    hideMenu(multiselect);
                    toggle.setAttribute("aria-expanded", "false");
                }
            });
        }

        inputs.forEach((input) => {
            input.addEventListener("change", () => {
                renderTags(multiselect);
                updateMenuPosition();
            });
        });
    });
}

setupTagMultiselects();

if (window.financeTagMultiselectGlobalReady !== "true") {
    window.financeTagMultiselectGlobalReady = "true";

    document.addEventListener("click", (event) => {
        if (!event.target.closest("[data-tag-multiselect]")) {
            document.querySelectorAll("[data-tag-multiselect]").forEach((multiselect) => {
                hideMenu(multiselect);
                const toggle = multiselect.querySelector("[data-tag-multiselect-toggle]");
                if (toggle) {
                    toggle.setAttribute("aria-expanded", "false");
                }
            });
        }
    });

    window.addEventListener("scroll", updateMenuPosition);
    window.addEventListener("resize", updateMenuPosition);
}
