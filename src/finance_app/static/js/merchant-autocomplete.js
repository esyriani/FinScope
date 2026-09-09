const merchantAutocompleteCloseEvent = "finance:merchant-autocomplete-close";

function merchantAutocompleteTranslate(message) {
    if (typeof window.financeTranslate === "function") {
        return window.financeTranslate(message);
    }
    return message;
}

function merchantAutocompleteControls(root = document) {
    return [
        ...(root.matches?.("[data-merchant-autocomplete]") ? [root] : []),
        ...Array.from(root.querySelectorAll("[data-merchant-autocomplete]")),
    ];
}

function merchantAutocompleteControlForEvent(event) {
    return typeof event.target?.closest === "function" ? event.target.closest("[data-merchant-autocomplete]") : null;
}

function setupMerchantAutocompleteGlobalListeners() {
    if (window.financeMerchantAutocompleteGlobalReady === "true") {
        return;
    }

    window.financeMerchantAutocompleteGlobalReady = "true";
    document.addEventListener("click", (event) => {
        const currentControl = merchantAutocompleteControlForEvent(event);
        document.querySelectorAll("[data-merchant-autocomplete]").forEach((control) => {
            if (control !== currentControl) {
                control.dispatchEvent(new CustomEvent(merchantAutocompleteCloseEvent));
            }
        });
    });
}

function setupMerchantAutocomplete(root = document) {
    merchantAutocompleteControls(root).forEach((control) => {
        if (control.dataset.merchantAutocompleteReady === "true") return;

        const input = control.querySelector("[data-merchant-autocomplete-input]");
        const idInput = control.querySelector("[data-merchant-autocomplete-id]");
        const menu = control.querySelector("[data-merchant-autocomplete-menu]");
        const suggestionsUrl = control.dataset.suggestionsUrl;
        if (!input || !idInput || !menu || !suggestionsUrl) return;

        control.dataset.merchantAutocompleteReady = "true";
        let selectedLabel = input.dataset.selectedMerchantLabel || input.value || "";
        let activeIndex = -1;
        let suggestions = [];
        let debounceId = 0;
        let abortController = null;

        function merchantAutocompleteOptionId(index) {
            return `${menu.id || input.id || "merchant-autocomplete"}-option-${index}`;
        }

        function setActiveDescendant(activeOption) {
            if (activeOption?.id) {
                input.setAttribute("aria-activedescendant", activeOption.id);
            } else {
                input.removeAttribute("aria-activedescendant");
            }
        }

        function setExpanded(expanded) {
            input.setAttribute("aria-expanded", expanded ? "true" : "false");
            menu.hidden = !expanded;
            if (!expanded) {
                setActiveDescendant(null);
            }
        }

        function clearMenu() {
            suggestions = [];
            activeIndex = -1;
            menu.replaceChildren();
            setExpanded(false);
        }

        function renderStatus(message) {
            setActiveDescendant(null);
            const status = document.createElement("div");
            status.className = "merchant-autocomplete-status";
            status.setAttribute("role", "option");
            status.setAttribute("aria-disabled", "true");
            status.textContent = message;
            menu.replaceChildren(status);
            setExpanded(true);
        }

        function updateActiveOption() {
            const options = Array.from(menu.querySelectorAll("[data-merchant-autocomplete-option]"));
            let activeOption = null;
            options.forEach((option, index) => {
                const active = index === activeIndex;
                option.classList.toggle("active", active);
                option.setAttribute("aria-selected", active ? "true" : "false");
                if (active) {
                    activeOption = option;
                }
            });
            setActiveDescendant(activeOption);
        }

        function selectSuggestion(suggestion) {
            input.value = suggestion.label;
            idInput.value = String(suggestion.id);
            selectedLabel = suggestion.label;
            input.dataset.selectedMerchantLabel = suggestion.label;
            clearMenu();
        }

        function renderSuggestions(items) {
            suggestions = items;
            activeIndex = -1;
            menu.replaceChildren();
            setActiveDescendant(null);
            if (!items.length) {
                renderStatus(merchantAutocompleteTranslate("No merchants found."));
                return;
            }

            items.forEach((suggestion, index) => {
                const option = document.createElement("button");
                option.type = "button";
                option.tabIndex = -1;
                option.className = "merchant-autocomplete-option";
                option.id = merchantAutocompleteOptionId(index);
                option.setAttribute("role", "option");
                option.setAttribute("aria-selected", "false");
                option.dataset.merchantAutocompleteOption = "true";
                option.textContent = suggestion.label;
                option.addEventListener("mousedown", (event) => event.preventDefault());
                option.addEventListener("click", () => selectSuggestion(suggestion));
                menu.appendChild(option);
            });
            setExpanded(true);
        }

        async function fetchSuggestions(query) {
            if (abortController) {
                abortController.abort();
            }
            abortController = new AbortController();
            const url = new URL(suggestionsUrl, window.location.origin);
            url.searchParams.set("q", query);
            if (control.dataset.suggestionsLimit) {
                url.searchParams.set("limit", control.dataset.suggestionsLimit);
            }

            try {
                const response = await fetch(url, {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                    signal: abortController.signal,
                });
                if (!response.ok) {
                    throw new Error(merchantAutocompleteTranslate("Merchant suggestions request failed."));
                }
                const payload = await response.json();
                renderSuggestions(Array.isArray(payload.suggestions) ? payload.suggestions : []);
            } catch (error) {
                if (error.name === "AbortError") return;
                renderStatus(merchantAutocompleteTranslate("Merchant suggestions unavailable."));
            }
        }

        function scheduleFetch() {
            const query = input.value.trim();
            window.clearTimeout(debounceId);
            if (query !== selectedLabel) {
                idInput.value = "";
            }
            if (query.length < 2) {
                clearMenu();
                return;
            }
            debounceId = window.setTimeout(() => fetchSuggestions(query), 160);
        }

        input.addEventListener("input", scheduleFetch);
        input.addEventListener("focus", scheduleFetch);
        input.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                clearMenu();
                return;
            }
            if (!suggestions.length || menu.hidden) return;

            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const step = event.key === "ArrowDown" ? 1 : -1;
                activeIndex = (activeIndex + step + suggestions.length) % suggestions.length;
                updateActiveOption();
            } else if (event.key === "Enter" && activeIndex >= 0) {
                event.preventDefault();
                selectSuggestion(suggestions[activeIndex]);
            }
        });
        control.addEventListener(merchantAutocompleteCloseEvent, clearMenu);
    });
}

window.financeApp?.registerInitializer("merchant.autocomplete", setupMerchantAutocomplete);
setupMerchantAutocompleteGlobalListeners();

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setupMerchantAutocomplete());
} else {
    setupMerchantAutocomplete();
}
