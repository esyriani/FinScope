function setupStatementTypesEditor() {
    const table = document.querySelector("[data-statement-types-table]");
    const addButton = document.querySelector("[data-add-statement-type]");
    const tbody = table?.querySelector("tbody");

    if (!table || !addButton || !tbody) {
        return;
    }

    function t(message, variables) {
        return window.financeTranslate ? window.financeTranslate(message, variables) : message;
    }

    function selectOptions(selectName, selectedValue) {
        const sourceSelect = table.querySelector(`select[name="${selectName}"]`);
        if (!sourceSelect) {
            return "";
        }

        return Array.from(sourceSelect.options)
            .map(
                (option) => `
            <option value="${escapeHtml(option.value)}" ${option.value === selectedValue ? "selected" : ""}>
                ${escapeHtml(option.textContent)}
            </option>
        `
            )
            .join("");
    }

    function statementTypeRow(
        name = "",
        parserType = "credit_card",
        importMode = "ledger",
        defaultAccountType = "credit_card"
    ) {
        return `
            <tr>
                <td>
                    <input type="hidden" name="statement_type_ids" value="">
                    <input class="form-control" type="text" name="statement_type_names" value="${escapeHtml(name)}" placeholder="${escapeHtml(t("Credit card"))}">
                    <div class="form-text">${escapeHtml(t("Names appear in upload and statement history."))}</div>
                </td>
                <td>
                    <select class="form-select" name="statement_type_parser_types">
                        ${selectOptions("statement_type_parser_types", parserType)}
                    </select>
                    <div class="form-text">${escapeHtml(t("Choose how imported rows are interpreted."))}</div>
                </td>
                <td>
                    <select class="form-select" name="statement_type_import_modes">
                        ${selectOptions("statement_type_import_modes", importMode)}
                    </select>
                    <div class="form-text">${escapeHtml(t("Choose whether uploads create ledger rows or enrich existing rows."))}</div>
                </td>
                <td>
                    <select class="form-select" name="statement_type_default_account_types">
                        ${selectOptions("statement_type_default_account_types", defaultAccountType)}
                    </select>
                    <div class="form-text">${escapeHtml(t("Default role for accounts created from this statement type."))}</div>
                </td>
                <td class="text-end">
                    <button class="btn btn-outline-danger" type="button" data-remove-statement-type aria-label="${escapeHtml(t("Remove"))}" title="${escapeHtml(t("Remove"))}">
                        <i class="bi bi-trash" aria-hidden="true"></i>
                    </button>
                </td>
            </tr>
        `;
    }

    addButton.addEventListener("click", () => {
        tbody.insertAdjacentHTML("beforeend", statementTypeRow());
        const newRow = tbody.lastElementChild;
        newRow?.querySelector('input[name="statement_type_names"]')?.focus();
    });

    table.addEventListener("click", (event) => {
        const removeButton = event.target.closest("[data-remove-statement-type]");
        if (!removeButton) {
            return;
        }

        const rows = Array.from(tbody.querySelectorAll("tr"));
        const row = removeButton.closest("tr");
        if (rows.length > 1) {
            row?.remove();
            return;
        }

        row?.querySelectorAll("input").forEach((input) => {
            input.value = "";
        });
    });
}

window.financeApp?.registerInitializer("settings.statement-types-editor", setupStatementTypesEditor);

setupStatementTypesEditor();
