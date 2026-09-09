(function () {
    function taxonomyItemFromSource(source) {
        const itemSource = source?.closest("[data-taxonomy-item]");
        if (!itemSource) {
            return null;
        }

        try {
            return JSON.parse(itemSource.dataset.taxonomyItem || "{}");
        } catch (_error) {
            return null;
        }
    }

    function setFieldValue(modal, name, value) {
        modal.querySelectorAll(`[data-taxonomy-field="${name}"]`).forEach((field) => {
            field.value = value == null ? "" : String(value);
        });
    }

    function populateTaxonomyModal(modal, source) {
        const item = taxonomyItemFromSource(source);
        if (!item) {
            return;
        }

        setFieldValue(modal, "id", item.id);
        setFieldValue(modal, "name", item.name);
        setFieldValue(modal, "color", item.color || "#64748b");
        setFieldValue(modal, "description", item.description);
        setFieldValue(modal, "instruction", item.instruction);

        const deleteName = modal.querySelector("[data-taxonomy-delete-name]");
        if (deleteName) {
            deleteName.textContent = item.name || "";
        }
    }

    function setupTaxonomyModals(root = document) {
        root.querySelectorAll(
            "[data-taxonomy-edit-modal], [data-taxonomy-view-modal], [data-taxonomy-delete-modal]"
        ).forEach((modal) => {
            if (modal.dataset.taxonomyModalReady === "true") {
                return;
            }

            modal.dataset.taxonomyModalReady = "true";
            modal.addEventListener("show.bs.modal", (event) => {
                populateTaxonomyModal(modal, event.relatedTarget || document.activeElement);
            });
        });
    }

    window.financeApp.registerInitializer("taxonomy.modals", setupTaxonomyModals);
    setupTaxonomyModals();
})();
