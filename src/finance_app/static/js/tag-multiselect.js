function renderTags(multiselect) {
    const tagsContainer = multiselect.querySelector("[data-tag-multiselect-tags]");
    const checkedInputs = optionInputs(multiselect).filter((input) => input.checked);
    const placeholder = multiselect.dataset.placeholder || "Select";
    const presetSummaryLabel = multiselect.dataset.selectPresetSummaryLabel || "";

    if (!tagsContainer) return;

    tagsContainer.innerHTML = "";

    if (checkedInputs.length === 0) {
        const placeholderSpan = document.createElement("span");
        placeholderSpan.className = "text-muted";
        placeholderSpan.textContent = placeholder;
        tagsContainer.appendChild(placeholderSpan);
    } else if (presetSummaryLabel && selectionMatchesPreset(multiselect)) {
        tagsContainer.appendChild(
            renderedTag(presetSummaryLabel, () => {
                setPresetOptions(multiselect, false);
            })
        );
    } else {
        checkedInputs.forEach((input) => {
            const label = input.nextElementSibling;
            const labelText = label ? label.textContent.trim() : input.value;

            tagsContainer.appendChild(
                renderedTag(labelText, () => {
                    input.checked = false;
                    renderTags(multiselect);
                    updateBulkOptionStates(multiselect);
                    updateMenuPosition();
                })
            );
        });
    }
}

function renderedTag(labelText, removeHandler) {
    const tag = document.createElement("span");
    tag.className = "tag-multiselect-tag";

    const textSpan = document.createElement("span");
    textSpan.textContent = labelText;

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "tag-multiselect-remove";
    closeBtn.setAttribute("aria-label", financeTranslate("Remove {label}", { label: labelText }));
    closeBtn.textContent = "x";
    closeBtn.addEventListener("click", (event) => {
        event.stopPropagation();
        removeHandler();
    });

    tag.appendChild(textSpan);
    tag.appendChild(closeBtn);
    return tag;
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

function tagMultiselectControl(multiselect) {
    return multiselect.querySelector("[data-tag-multiselect-control]");
}

function tagMultiselectToggle(multiselect) {
    return multiselect.querySelector("[data-tag-multiselect-toggle]");
}

function tagMultiselectMenu(multiselect) {
    return multiselect.querySelector("[data-tag-multiselect-menu]");
}

function menuIsOpen(multiselect) {
    const menu = tagMultiselectMenu(multiselect);
    return Boolean(menu && !menu.hidden && menu.style.display === "block");
}

function menuCheckboxInputs(multiselect) {
    const menu = tagMultiselectMenu(multiselect);
    if (!menu) {
        return [];
    }
    return Array.from(menu.querySelectorAll("input[type='checkbox']")).filter((input) => !input.disabled);
}

function focusMenuOption(multiselect, position = "first") {
    const inputs = menuCheckboxInputs(multiselect);
    if (inputs.length === 0) {
        tagMultiselectMenu(multiselect)?.focus();
        return;
    }

    const index = position === "last" ? inputs.length - 1 : 0;
    inputs[index].focus();
}

function moveMenuFocus(multiselect, step) {
    const inputs = menuCheckboxInputs(multiselect);
    if (inputs.length === 0) {
        return;
    }

    const currentIndex = inputs.indexOf(document.activeElement);
    const nextIndex = currentIndex === -1 ? 0 : (currentIndex + step + inputs.length) % inputs.length;
    inputs[nextIndex].focus();
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

function presetOptionGroups(multiselect, options = enabledOptionInputs(multiselect)) {
    const excludedValues = presetExcludedValues(multiselect);

    return {
        excludedOptions: options.filter((option) => excludedValues.has(option.value)),
        presetOptions: options.filter((option) => !excludedValues.has(option.value)),
    };
}

function selectionMatchesPreset(multiselect) {
    const { excludedOptions, presetOptions } = presetOptionGroups(multiselect, optionInputs(multiselect));

    return (
        presetOptions.length > 0 &&
        presetOptions.every((option) => option.checked) &&
        !excludedOptions.some((option) => option.checked)
    );
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

    const { excludedOptions, presetOptions } = presetOptionGroups(multiselect);
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
    const anchor = tagMultiselectControl(multiselect) || tagMultiselectToggle(multiselect);
    const menu = tagMultiselectMenu(multiselect);

    if (!anchor || !menu) return;

    const rect = anchor.getBoundingClientRect();
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

function setMenuExpanded(multiselect, expanded, focusTarget = "") {
    const menu = tagMultiselectMenu(multiselect);
    const toggle = tagMultiselectToggle(multiselect);
    if (menu) {
        menu.hidden = !expanded;
        menu.style.display = expanded ? "block" : "none";
        multiselect.classList.toggle("open", expanded);
    }
    if (toggle) {
        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    }

    if (expanded) {
        positionMenu(multiselect);
        if (focusTarget) {
            focusMenuOption(multiselect, focusTarget);
        }
    } else if (focusTarget === "toggle") {
        toggle?.focus();
    }
}

function showMenu(multiselect, focusTarget = "") {
    setMenuExpanded(multiselect, true, focusTarget);
}

function hideMenu(multiselect, focusTarget = "") {
    setMenuExpanded(multiselect, false, focusTarget);
}

function updateMenuPosition() {
    document.querySelectorAll("[data-tag-multiselect]").forEach((multiselect) => {
        if (menuIsOpen(multiselect)) {
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
        const control = tagMultiselectControl(multiselect);
        const toggle = tagMultiselectToggle(multiselect);
        const menu = tagMultiselectMenu(multiselect);
        const inputs = optionInputs(multiselect);
        const selectAll = selectAllInput(multiselect);
        const preset = presetInput(multiselect);

        renderTags(multiselect);
        updateBulkOptionStates(multiselect);

        if (control && toggle) {
            control.addEventListener("click", (event) => {
                if (multiselect.dataset.disabled === "true") {
                    return;
                }
                if (
                    event.target.closest(
                        "[data-tag-multiselect-toggle], .tag-multiselect-remove, input, label, a, button"
                    )
                ) {
                    return;
                }
                toggle.click();
            });
        }

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

                if (menuIsOpen(multiselect)) {
                    hideMenu(multiselect);
                } else {
                    showMenu(multiselect);
                }
            });

            toggle.addEventListener("keydown", (event) => {
                if (multiselect.dataset.disabled === "true") {
                    return;
                }

                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    toggle.click();
                    return;
                }

                if (event.key === "ArrowDown" || event.key === "Down") {
                    event.preventDefault();
                    showMenu(multiselect, "first");
                    return;
                }

                if (event.key === "ArrowUp" || event.key === "Up") {
                    event.preventDefault();
                    showMenu(multiselect, "last");
                    return;
                }

                if (event.key === "Escape") {
                    hideMenu(multiselect);
                }
            });
        }

        menu?.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                event.preventDefault();
                hideMenu(multiselect, "toggle");
                return;
            }

            if (event.key === "ArrowDown" || event.key === "Down") {
                event.preventDefault();
                moveMenuFocus(multiselect, 1);
                return;
            }

            if (event.key === "ArrowUp" || event.key === "Up") {
                event.preventDefault();
                moveMenuFocus(multiselect, -1);
                return;
            }

            if (event.key === "Home") {
                event.preventDefault();
                focusMenuOption(multiselect, "first");
                return;
            }

            if (event.key === "End") {
                event.preventDefault();
                focusMenuOption(multiselect, "last");
            }
        });

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
            });
        }
    });

    window.addEventListener("scroll", updateMenuPosition);
    window.addEventListener("resize", updateMenuPosition);
}
