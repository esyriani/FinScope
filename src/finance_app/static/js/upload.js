function setupUploadAccountBehavior() {
    const form = document.querySelector("[data-upload-form]");
    const statementTypeSelect = form?.querySelector("[data-statement-type-select]");
    const accountTypeSelect = form?.querySelector("[data-account-type-select]");
    const paidFromField = form?.querySelector("[data-paid-from-field]");
    const interacDirectionField = form?.querySelector("[data-interac-direction-field]");
    const interacDirectionSelect = form?.querySelector('select[name="interac_direction"]');
    const interacWarning = form?.querySelector("[data-interac-warning]");

    if (
        !form ||
        !statementTypeSelect ||
        !accountTypeSelect ||
        !paidFromField ||
        !interacDirectionField ||
        !interacDirectionSelect ||
        !interacWarning
    ) {
        return;
    }

    if (form.dataset.uploadAccountBehaviorReady === "true") {
        return;
    }

    form.dataset.uploadAccountBehaviorReady = "true";

    const updateFromStatementType = () => {
        const selectedOption = statementTypeSelect.selectedOptions[0];
        const defaultAccountType = selectedOption?.dataset.accountType || "checking";
        // Statement import type chooses parser/import behavior; account
        // reporting role controls report classification. Keep the role aligned
        // with the selected type by default, while still allowing overrides.
        if (accountTypeSelect.value !== defaultAccountType) {
            accountTypeSelect.value = defaultAccountType;
        }
        updatePaidFromVisibility();
        updateInteracDirectionVisibility();
    };

    const updatePaidFromVisibility = () => {
        const showPaidFrom = accountTypeSelect.value === "credit_card";
        paidFromField.classList.toggle("d-none", !showPaidFrom);
    };

    const updateInteracDirectionVisibility = () => {
        const selectedOption = statementTypeSelect.selectedOptions[0];
        const showDirection = selectedOption?.dataset.parserType === "interac_etransfer";
        interacDirectionField.classList.toggle("d-none", !showDirection);
        interacWarning.classList.toggle("d-none", !showDirection);
        if (!showDirection) {
            interacDirectionSelect.value = "auto";
        }
    };

    statementTypeSelect.addEventListener("change", updateFromStatementType);
    accountTypeSelect.addEventListener("change", updatePaidFromVisibility);
    updateFromStatementType();
}

function setupUploadFileSelectionFeedback(root = document) {
    const form = root.querySelector?.("[data-upload-form]") || document.querySelector("[data-upload-form]");
    if (!form || form.dataset.uploadFileSelectionReady === "true") {
        return;
    }

    const fileInput = form.querySelector("[data-upload-file-input]");
    const browseButton = form.querySelector("[data-upload-file-browse]");
    const fileNameNode = form.querySelector("[data-upload-file-name]");

    if (!fileInput || !browseButton || !fileNameNode) {
        return;
    }

    form.dataset.uploadFileSelectionReady = "true";

    const translate = (message, variables) =>
        window.financeTranslate ? window.financeTranslate(message, variables) : message;

    let selectionBusyToken = null;
    let hideSelectionTimer = null;

    const clearHideSelectionTimer = () => {
        if (!hideSelectionTimer) {
            return;
        }

        window.clearTimeout(hideSelectionTimer);
        hideSelectionTimer = null;
    };

    const selectedFileLabel = () => {
        const files = Array.from(fileInput.files || []);
        if (!files.length) {
            return translate("No file selected");
        }

        return files.map((file) => file.name).join(", ");
    };

    const updateFileName = () => {
        const label = selectedFileLabel();
        fileNameNode.textContent = label;
        fileNameNode.title = label;
    };

    const hideSelectionBusy = (delayMs = 350) => {
        clearHideSelectionTimer();
        if (!selectionBusyToken) {
            browseButton.removeAttribute("aria-busy");
            return;
        }

        hideSelectionTimer = window.setTimeout(
            () => {
                window.hideBusyOverlay?.(selectionBusyToken);
                selectionBusyToken = null;
                hideSelectionTimer = null;
                browseButton.removeAttribute("aria-busy");
            },
            Math.max(0, delayMs)
        );
    };

    const showSelectionBusy = () => {
        clearHideSelectionTimer();
        browseButton.setAttribute("aria-busy", "true");
        if (selectionBusyToken) {
            return;
        }

        selectionBusyToken =
            window.showBusyOverlay?.({
                immediate: true,
                message: translate("Opening statement..."),
            }) || null;
    };

    const openFilePickerAfterOverlayPaint = () => {
        const openPicker = () => {
            fileInput.click();
            window.setTimeout(() => {
                if (selectionBusyToken && document.hasFocus()) {
                    hideSelectionBusy(0);
                }
            }, 1200);
        };

        window.requestAnimationFrame(() => {
            window.requestAnimationFrame(openPicker);
        });
    };

    browseButton.addEventListener("click", () => {
        showSelectionBusy();
        openFilePickerAfterOverlayPaint();
    });

    fileInput.addEventListener("change", () => {
        updateFileName();
        hideSelectionBusy(500);
    });

    fileInput.addEventListener("cancel", () => hideSelectionBusy(250));
    window.addEventListener("focus", () => {
        if (selectionBusyToken) {
            hideSelectionBusy(650);
        }
    });

    updateFileName();
}

function setupUploadPreview(root = document) {
    const form = root.querySelector?.("[data-upload-form]") || document.querySelector("[data-upload-form]");
    if (!form || form.dataset.uploadPreviewReady === "true") {
        return;
    }

    const previewUrl = form.dataset.previewUrl;
    const modalElement = document.getElementById("upload-preview-modal");
    const dateOrderInput = form.querySelector("[data-upload-date-order]");
    const submitButton = form.querySelector("[data-upload-submit]");

    if (!previewUrl || !modalElement || !dateOrderInput) {
        return;
    }

    form.dataset.uploadPreviewReady = "true";

    const modal = window.bootstrap?.Modal ? window.bootstrap.Modal.getOrCreateInstance(modalElement) : null;
    const errorAlert = modalElement.querySelector("[data-upload-preview-error]");
    const countNode = modalElement.querySelector("[data-upload-preview-count]");
    const ignoredNode = modalElement.querySelector("[data-upload-preview-ignored]");
    const dateRangeNode = modalElement.querySelector("[data-upload-preview-date-range]");
    const dateFormatNode = modalElement.querySelector("[data-upload-preview-date-format]");
    const rowsNode = modalElement.querySelector("[data-upload-preview-rows]");
    const emptyNode = modalElement.querySelector("[data-upload-preview-empty]");
    const dateChoiceNode = modalElement.querySelector("[data-upload-preview-date-choice]");
    const dateMessageNode = modalElement.querySelector("[data-upload-preview-date-message]");
    const confirmButton = modalElement.querySelector("[data-upload-preview-confirm]");
    const dateOptions = Array.from(modalElement.querySelectorAll("[data-upload-preview-date-option]"));

    let currentPreview = null;
    let selectedDateOrder = "";

    const translate = (message, variables) =>
        window.financeTranslate ? window.financeTranslate(message, variables) : message;

    const showError = (message) => {
        if (!errorAlert) {
            return;
        }
        errorAlert.textContent = translate(message || "Preview could not be loaded.");
        errorAlert.classList.remove("d-none");
    };

    const clearError = () => {
        errorAlert?.classList.add("d-none");
        if (errorAlert) {
            errorAlert.textContent = "";
        }
    };

    const optionLabel = (value) => {
        const option = (currentPreview?.date_format?.options || []).find((item) => item.value === value);
        return translate(option?.label || (value === "month_first" ? "MM/DD/YYYY" : "DD/MM/YYYY"));
    };

    const formatDateRange = (dateRange) => {
        const earliest = dateRange?.earliest || "";
        const latest = dateRange?.latest || "";
        if (!earliest && !latest) {
            return translate("n/a");
        }
        if (earliest === latest || !latest) {
            return earliest;
        }
        if (!earliest) {
            return latest;
        }
        return `${earliest} - ${latest}`;
    };

    const rowDateForOrder = (row, dateOrder) => {
        if (!dateOrder && currentPreview?.date_format?.requires_choice) {
            return translate("Choose date format");
        }
        if (dateOrder === "month_first" && row.month_first_date) {
            return row.month_first_date;
        }
        if (dateOrder === "day_first" && row.day_first_date) {
            return row.day_first_date;
        }
        return row.parsed_date || "";
    };

    const updateDateDependentPreview = () => {
        const dateOrder = selectedDateOrder || currentPreview?.date_format?.effective_order || "";
        if (dateFormatNode) {
            dateFormatNode.textContent = dateOrder ? optionLabel(dateOrder) : translate("Auto-detect");
        }
        if (dateRangeNode) {
            const range = currentPreview?.date_ranges?.[dateOrder] || currentPreview?.date_range;
            dateRangeNode.textContent =
                currentPreview?.date_format?.requires_choice && !dateOrder
                    ? translate("Choose date format")
                    : formatDateRange(range);
        }

        modalElement.querySelectorAll("[data-upload-preview-parsed-date]").forEach((cell) => {
            const row = {
                parsed_date: cell.dataset.autoDate || "",
                month_first_date: cell.dataset.monthFirstDate || "",
                day_first_date: cell.dataset.dayFirstDate || "",
            };
            cell.textContent = rowDateForOrder(row, dateOrder);
        });

        if (confirmButton) {
            confirmButton.disabled = Boolean(currentPreview?.date_format?.requires_choice && !dateOrder);
        }
    };

    const renderRows = (rows) => {
        if (!rowsNode || !emptyNode) {
            return;
        }

        if (!rows.length) {
            rowsNode.replaceChildren();
            emptyNode.classList.remove("d-none");
            return;
        }

        emptyNode.classList.add("d-none");
        rowsNode.replaceChildren(
            ...rows.map((row) => {
                const tableRow = document.createElement("tr");
                const rawDate = document.createElement("td");
                rawDate.textContent = row.raw_date || "";

                const parsedDate = document.createElement("td");
                parsedDate.dataset.uploadPreviewParsedDate = "";
                parsedDate.dataset.autoDate = row.parsed_date || "";
                parsedDate.dataset.monthFirstDate = row.month_first_date || "";
                parsedDate.dataset.dayFirstDate = row.day_first_date || "";
                parsedDate.textContent = rowDateForOrder(
                    row,
                    selectedDateOrder || currentPreview?.date_format?.effective_order || ""
                );

                const description = document.createElement("td");
                description.textContent = row.description || "";
                const amount = document.createElement("td");
                amount.className = "text-end";
                amount.textContent = row.amount || "";

                tableRow.append(rawDate, parsedDate, description, amount);
                return tableRow;
            })
        );
    };

    const updateDateChoice = () => {
        const dateFormat = currentPreview?.date_format || {};
        const showChoice = Boolean(dateFormat.has_date_order_dates || dateFormat.has_slash_dates);
        dateChoiceNode?.classList.toggle("d-none", !showChoice);

        selectedDateOrder = dateFormat.effective_order || "";
        dateOptions.forEach((option) => {
            option.checked = option.value === selectedDateOrder;
        });

        if (dateMessageNode) {
            if (dateFormat.requires_choice) {
                dateMessageNode.textContent = translate("Choose the date format before importing.");
            } else if (dateFormat.source === "detected") {
                dateMessageNode.textContent = translate("Date format detected from unambiguous rows.");
            } else if (dateFormat.source === "selected") {
                dateMessageNode.textContent = translate("Date format selected for this import.");
            } else {
                dateMessageNode.textContent = translate("No date-format choice is needed for this file.");
            }
        }

        if (!showChoice) {
            selectedDateOrder = "";
            dateOrderInput.value = "auto";
        }
        updateDateDependentPreview();
    };

    const renderPreview = (preview) => {
        currentPreview = preview;
        clearError();
        if (countNode) {
            countNode.textContent = String(preview.transaction_count ?? 0);
        }
        if (ignoredNode) {
            ignoredNode.textContent = String(preview.ignored_rows ?? 0);
        }
        renderRows(preview.preview_rows || []);
        updateDateChoice();
    };

    const submitConfirmedUpload = () => {
        const dateFormat = currentPreview?.date_format || {};
        const dateOrder = selectedDateOrder || dateFormat.effective_order || "";
        dateOrderInput.value = dateOrder || "auto";
        form.dataset.uploadPreviewConfirmed = "true";
        modal?.hide();
        if (form.requestSubmit) {
            form.requestSubmit();
        } else {
            form.submit();
        }
    };

    dateOptions.forEach((option) => {
        option.addEventListener("change", () => {
            selectedDateOrder = option.checked ? option.value : selectedDateOrder;
            dateOrderInput.value = selectedDateOrder || "auto";
            updateDateDependentPreview();
        });
    });

    confirmButton?.addEventListener("click", submitConfirmedUpload);

    form.addEventListener("submit", async (event) => {
        if (form.dataset.uploadPreviewConfirmed === "true") {
            form.dataset.uploadPreviewConfirmed = "false";
            return;
        }
        if (!form.reportValidity()) {
            return;
        }

        event.preventDefault();
        dateOrderInput.value = "auto";
        clearError();
        if (confirmButton) {
            confirmButton.disabled = true;
        }
        if (submitButton) {
            submitButton.disabled = true;
            submitButton.setAttribute("aria-busy", "true");
        }

        const previewBusyToken =
            window.showBusyOverlay?.({
                delayMs: 0,
                message: translate("Preparing statement preview..."),
            }) || null;

        try {
            const formData = new FormData(form);
            const response = await fetch(previewUrl, {
                method: "POST",
                headers: {
                    "X-CSRF-Token": getCsrfToken(),
                    "X-Requested-With": "fetch",
                },
                body: formData,
                credentials: "same-origin",
            });
            const data = await response.json();
            if (!response.ok || data.ok === false) {
                throw new Error(data.message || translate("Preview could not be loaded."));
            }
            renderPreview(data.preview || {});
        } catch (error) {
            currentPreview = null;
            renderRows([]);
            showError(error?.message || "Preview could not be loaded.");
            if (confirmButton) {
                confirmButton.disabled = true;
            }
        } finally {
            window.hideBusyOverlay?.(previewBusyToken);
            if (submitButton) {
                submitButton.disabled = false;
                submitButton.removeAttribute("aria-busy");
            }
            modal?.show();
        }
    });
}

window.financeApp?.registerInitializer("upload.account-behavior", setupUploadAccountBehavior);
window.financeApp?.registerInitializer("upload.file-selection-feedback", setupUploadFileSelectionFeedback);
window.financeApp?.registerInitializer("upload.preview", setupUploadPreview);

setupUploadAccountBehavior();
setupUploadFileSelectionFeedback();
setupUploadPreview();
