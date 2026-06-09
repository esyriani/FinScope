function renderTags(multiselect) {
    const tagsContainer = multiselect.querySelector("[data-tag-multiselect-tags]");
    const checkedInputs = optionInputs(multiselect).filter((input) => input.checked);
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
                updateBulkOptionStates(multiselect);
                updateMenuPosition();
            });

            tag.appendChild(textSpan);
            tag.appendChild(closeBtn);
            tagsContainer.appendChild(tag);
        });
    }
}

function optionInputs(multiselect) {
    return Array.from(
        multiselect.querySelectorAll(
            "input[type='checkbox']:not([data-tag-multiselect-select-all]):not([data-tag-multiselect-preset])"
        )
    );
}

function enabledOptionInputs(multiselect) {
    return optionInputs(multiselect).filter((input) => !input.disabled);
}

function selectAllInput(multiselect) {
    return multiselect.querySelector("[data-tag-multiselect-select-all]");
}

function presetInput(multiselect) {
    return multiselect.querySelector("[data-tag-multiselect-preset]");
}

function presetExcludedValues(multiselect) {
    const rawValue = multiselect.dataset.selectPresetExcludeValues || "[]";

    try {
        const parsedValues = JSON.parse(rawValue);
        if (Array.isArray(parsedValues)) {
            return new Set(parsedValues.map((value) => String(value)));
        }
    } catch (_error) {
        return new Set();
    }

    return new Set();
}

function ensureSelectAllOption(multiselect) {
    const labelText = multiselect.dataset.selectAllLabel;
    const menu = multiselect.querySelector("[data-tag-multiselect-menu]");
    if (!labelText || !menu || selectAllInput(multiselect)) {
        return;
    }

    const label = document.createElement("label");
    label.className = "tag-multiselect-option tag-multiselect-option-bulk tag-multiselect-option-all";

    const input = document.createElement("input");
    input.className = "form-check-input";
    input.type = "checkbox";
    input.setAttribute("data-tag-multiselect-select-all", "");

    const text = document.createElement("span");
    text.textContent = labelText;

    label.append(input, text);
    menu.prepend(label);
}

function ensurePresetOption(multiselect) {
    const labelText = multiselect.dataset.selectPresetLabel;
    const menu = multiselect.querySelector("[data-tag-multiselect-menu]");
    if (!labelText || !menu || presetInput(multiselect)) {
        return;
    }

    const label = document.createElement("label");
    label.className = "tag-multiselect-option tag-multiselect-option-bulk tag-multiselect-option-preset";

    const input = document.createElement("input");
    input.className = "form-check-input";
    input.type = "checkbox";
    input.setAttribute("data-tag-multiselect-preset", "");

    const text = document.createElement("span");
    text.textContent = labelText;

    label.append(input, text);

    const selectAllLabel = selectAllInput(multiselect)?.closest(".tag-multiselect-option");
    if (selectAllLabel && selectAllLabel.nextSibling) {
        menu.insertBefore(label, selectAllLabel.nextSibling);
    } else if (selectAllLabel) {
        menu.append(label);
    } else {
        menu.prepend(label);
    }
}

function updateBulkOptionBorders(multiselect) {
    const bulkOptions = Array.from(multiselect.querySelectorAll(".tag-multiselect-option-bulk"));
    bulkOptions.forEach((option) => option.classList.remove("tag-multiselect-option-bulk-end"));
    const lastBulkOption = bulkOptions[bulkOptions.length - 1];
    lastBulkOption?.classList.add("tag-multiselect-option-bulk-end");
}

function updateSelectAllState(multiselect) {
    const input = selectAllInput(multiselect);
    if (!input) {
        return;
    }

    const options = enabledOptionInputs(multiselect);
    const checkedCount = options.filter((option) => option.checked).length;
    input.disabled = options.length === 0;
    input.checked = options.length > 0 && checkedCount === options.length;
    input.indeterminate = checkedCount > 0 && checkedCount < options.length;
}

function updatePresetState(multiselect) {
    const input = presetInput(multiselect);
    if (!input) {
        return;
    }

    const excludedValues = presetExcludedValues(multiselect);
    const options = enabledOptionInputs(multiselect);
    const presetOptions = options.filter((option) => !excludedValues.has(option.value));
    const excludedOptions = options.filter((option) => excludedValues.has(option.value));
    const presetCheckedCount = presetOptions.filter((option) => option.checked).length;
    const hasExcludedChecked = excludedOptions.some((option) => option.checked);

    input.disabled = presetOptions.length === 0;
    input.checked = presetOptions.length > 0 && presetCheckedCount === presetOptions.length && !hasExcludedChecked;
    input.indeterminate = presetCheckedCount > 0 && (presetCheckedCount < presetOptions.length || hasExcludedChecked);
}

function updateBulkOptionStates(multiselect) {
    updateSelectAllState(multiselect);
    updatePresetState(multiselect);
}

function setAllOptions(multiselect, checked) {
    enabledOptionInputs(multiselect).forEach((input) => {
        input.checked = checked;
    });
    renderTags(multiselect);
    updateBulkOptionStates(multiselect);
    updateMenuPosition();
}

function setPresetOptions(multiselect, checked) {
    const excludedValues = presetExcludedValues(multiselect);
    enabledOptionInputs(multiselect).forEach((input) => {
        input.checked = checked && !excludedValues.has(input.value);
    });
    renderTags(multiselect);
    updateBulkOptionStates(multiselect);
    updateMenuPosition();
}

function positionMenu(multiselect) {
    const toggle = multiselect.querySelector("[data-tag-multiselect-toggle]");
    const menu = multiselect.querySelector("[data-tag-multiselect-menu]");

    if (!toggle || !menu) return;

    const rect = toggle.getBoundingClientRect();
    const viewportPadding = 8;
    const width = Math.min(rect.width, window.innerWidth - viewportPadding * 2);
    const left = Math.max(viewportPadding, Math.min(rect.left, window.innerWidth - width - viewportPadding));
    const spaceBelow = window.innerHeight - rect.bottom - viewportPadding;
    const spaceAbove = rect.top - viewportPadding;
    const openAbove = spaceBelow < 180 && spaceAbove > spaceBelow;
    const availableHeight = openAbove ? spaceAbove : spaceBelow;
    const maxHeight = Math.max(80, Math.min(300, availableHeight));

    menu.style.top = (openAbove ? Math.max(viewportPadding, rect.top - maxHeight) : rect.bottom) + "px";
    menu.style.left = left + "px";
    menu.style.width = width + "px";
    menu.style.maxHeight = maxHeight + "px";
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
    document.querySelectorAll("[data-tag-multiselect]").forEach((multiselect) => {
        const menu = multiselect.querySelector("[data-tag-multiselect-menu]");
        if (menu && menu.style.display === "block") {
            positionMenu(multiselect);
        }
    });
}

function setupTagMultiselects(root = document) {
    const multiselects = Array.from(root.querySelectorAll("[data-tag-multiselect]"));

    multiselects.forEach((multiselect) => {
        ensureSelectAllOption(multiselect);
        ensurePresetOption(multiselect);
        updateBulkOptionBorders(multiselect);
        if (multiselect.dataset.tagMultiselectReady === "true") {
            renderTags(multiselect);
            updateBulkOptionStates(multiselect);
            return;
        }

        multiselect.dataset.tagMultiselectReady = "true";
        const toggle = multiselect.querySelector("[data-tag-multiselect-toggle]");
        const inputs = optionInputs(multiselect);
        const selectAll = selectAllInput(multiselect);
        const preset = presetInput(multiselect);

        renderTags(multiselect);
        updateBulkOptionStates(multiselect);

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
                updateBulkOptionStates(multiselect);
                updateMenuPosition();
            });
        });

        selectAll?.addEventListener("change", () => {
            setAllOptions(multiselect, selectAll.checked);
        });

        preset?.addEventListener("change", () => {
            setPresetOptions(multiselect, preset.checked);
        });
    });
}

window.financeApp?.registerInitializer("tag-multiselect.controls", setupTagMultiselects);

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
