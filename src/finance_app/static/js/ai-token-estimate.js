const AI_TOKEN_ESTIMATE_FORM_SELECTOR = "form[data-ai-token-estimate-url]";
const AI_TOKEN_ESTIMATE_CONFIRMED_FIELD = "ai_token_estimate_confirmed";

const aiTokenEstimateState = {
    form: null,
    submitter: null,
    modal: null,
};

function translateAiTokenEstimate(message, variables = {}) {
    return window.financeTranslate ? window.financeTranslate(message, variables) : message;
}

function formatAiTokenNumber(value) {
    const numberValue = Number(value || 0);
    const boundedValue = Number.isFinite(numberValue) ? Math.max(0, Math.round(numberValue)) : 0;
    return new Intl.NumberFormat(window.financeLocale || "en-CA").format(boundedValue);
}

function formatAiTokenCount(value) {
    return translateAiTokenEstimate("{count} tokens", {
        count: formatAiTokenNumber(value),
    });
}

function formatAiTokenContextUsage(usedTokens, limitTokens, percentage) {
    if (Number.isFinite(percentage)) {
        return translateAiTokenEstimate("{used} / {limit} tokens, about {percent}% of the context window", {
            used: formatAiTokenNumber(usedTokens),
            limit: formatAiTokenNumber(limitTokens),
            percent: formatAiTokenNumber(percentage),
        });
    }
    return translateAiTokenEstimate("{used} / {limit} tokens", {
        used: formatAiTokenNumber(usedTokens),
        limit: formatAiTokenNumber(limitTokens),
    });
}

function aiTokenEstimateElement(tagName, className = "", text = "") {
    const element = document.createElement(tagName);
    if (className) {
        element.className = className;
    }
    if (text) {
        element.textContent = text;
    }
    return element;
}

function positiveAiTokenNumber(value) {
    const numberValue = Number(value || 0);
    return Number.isFinite(numberValue) ? Math.max(0, Math.round(numberValue)) : 0;
}

function appendAiTokenEstimateRow(container, label, value, options = {}) {
    const row = aiTokenEstimateElement(
        "div",
        `ai-token-estimate-row${options.emphasized ? " ai-token-estimate-row-emphasized" : ""}`
    );
    const labelNode = aiTokenEstimateElement("dt", "ai-token-estimate-label", label);
    const valueNode = aiTokenEstimateElement("dd", "ai-token-estimate-value", value);
    if (options.helper) {
        const helperNode = aiTokenEstimateElement("div", "ai-token-estimate-helper", options.helper);
        valueNode.append(helperNode);
    }

    row.append(labelNode, valueNode);
    container.append(row);
}

function aiTokenEstimateContextStatus(estimate) {
    const limitTokens = positiveAiTokenNumber(estimate.context_limit_tokens);
    const usedTokens = positiveAiTokenNumber(
        estimate.context_usage_tokens ?? estimate.max_batch_total_tokens ?? estimate.total_tokens
    );
    if (limitTokens <= 0 || usedTokens <= 0) {
        return null;
    }

    const ratio = usedTokens / limitTokens;
    const percent = Math.round(ratio * 100);
    if (ratio > 1) {
        return {
            alertClass: "alert-danger",
            label: translateAiTokenEstimate("Exceeds model limit"),
            message: translateAiTokenEstimate("This estimate exceeds the selected model's context limit."),
            usedTokens,
            limitTokens,
            percent,
        };
    }
    if (ratio > 0.85) {
        return {
            alertClass: "alert-warning",
            label: translateAiTokenEstimate("Near model limit"),
            message: translateAiTokenEstimate("This estimate is near the selected model's context limit."),
            usedTokens,
            limitTokens,
            percent,
        };
    }
    if (ratio > 0.6) {
        return {
            alertClass: "alert-warning",
            label: translateAiTokenEstimate("High AI usage"),
            message: translateAiTokenEstimate("It fits within the selected model's context limit."),
            usedTokens,
            limitTokens,
            percent,
        };
    }
    if (ratio > 0.25) {
        return {
            alertClass: "alert-info",
            label: translateAiTokenEstimate("Moderate AI usage"),
            message: translateAiTokenEstimate("It fits within the selected model's context limit."),
            usedTokens,
            limitTokens,
            percent,
        };
    }
    return {
        alertClass: "alert-success",
        label: translateAiTokenEstimate("Low AI usage"),
        message: translateAiTokenEstimate("It fits within the selected model's context limit."),
        usedTokens,
        limitTokens,
        percent,
    };
}

function setAiTokenEstimateHidden(modal, selector, hidden = true) {
    modal.querySelector(selector).classList.toggle("d-none", hidden);
}

function ensureAiTokenEstimateModal() {
    const existingModal = document.getElementById("ai-token-estimate-modal");
    if (existingModal) {
        return existingModal;
    }

    const modal = aiTokenEstimateElement("div", "modal fade");
    modal.id = "ai-token-estimate-modal";
    modal.tabIndex = -1;
    modal.setAttribute("aria-labelledby", "ai-token-estimate-modal-title");
    modal.setAttribute("aria-hidden", "true");

    const dialog = aiTokenEstimateElement("div", "modal-dialog modal-lg modal-dialog-fit-content");
    const content = aiTokenEstimateElement("div", "modal-content");
    const header = aiTokenEstimateElement("div", "modal-header");
    const title = aiTokenEstimateElement("h5", "modal-title", translateAiTokenEstimate("Review AI usage"));
    title.id = "ai-token-estimate-modal-title";
    const closeButton = aiTokenEstimateElement("button", "btn-close");
    closeButton.type = "button";
    closeButton.setAttribute("data-bs-dismiss", "modal");
    closeButton.setAttribute("aria-label", translateAiTokenEstimate("Close"));

    const body = aiTokenEstimateElement("div", "modal-body");
    const summary = aiTokenEstimateElement("div", "ai-token-estimate-summary");
    const message = aiTokenEstimateElement("p", "ai-token-estimate-message");
    message.dataset.aiTokenEstimateMessage = "";
    const contextMessage = aiTokenEstimateElement("p", "ai-token-estimate-context d-none");
    contextMessage.dataset.aiTokenEstimateContext = "";
    summary.append(message, contextMessage);

    const loading = aiTokenEstimateElement("div", "d-flex align-items-center gap-2 text-muted");
    loading.dataset.aiTokenEstimateLoading = "";
    const spinner = aiTokenEstimateElement("span", "spinner-border spinner-border-sm");
    spinner.setAttribute("aria-hidden", "true");
    const loadingText = aiTokenEstimateElement("span", "", translateAiTokenEstimate("Loading AI usage estimate..."));
    loading.append(spinner, loadingText);

    const error = aiTokenEstimateElement("div", "alert alert-danger d-none");
    error.dataset.aiTokenEstimateError = "";
    error.setAttribute("role", "alert");
    const usage = aiTokenEstimateElement("div", "alert ai-token-estimate-usage d-none");
    usage.dataset.aiTokenEstimateUsage = "";
    const warning = aiTokenEstimateElement("div", "alert alert-warning d-none");
    warning.dataset.aiTokenEstimateWarning = "";
    warning.setAttribute("role", "alert");
    const metrics = aiTokenEstimateElement("dl", "ai-token-estimate-list d-none");
    metrics.dataset.aiTokenEstimateMetrics = "";
    const details = aiTokenEstimateElement("details", "ai-token-estimate-details d-none");
    details.dataset.aiTokenEstimateDetails = "";
    const detailsSummary = aiTokenEstimateElement("summary", "", translateAiTokenEstimate("Technical details"));
    const detailsMetrics = aiTokenEstimateElement("dl", "ai-token-estimate-list ai-token-estimate-list-compact");
    detailsMetrics.dataset.aiTokenEstimateDetailsMetrics = "";
    details.append(detailsSummary, detailsMetrics);

    const footer = aiTokenEstimateElement("div", "modal-footer");
    const cancelButton = aiTokenEstimateElement(
        "button",
        "btn btn-outline-secondary",
        translateAiTokenEstimate("Cancel")
    );
    cancelButton.type = "button";
    cancelButton.setAttribute("data-bs-dismiss", "modal");
    const confirmButton = aiTokenEstimateElement("button", "btn btn-primary", translateAiTokenEstimate("Run AI"));
    confirmButton.type = "button";
    confirmButton.dataset.aiTokenEstimateConfirm = "";
    confirmButton.disabled = true;

    header.append(title, closeButton);
    body.append(summary, loading, error, usage, warning, metrics, details);
    footer.append(cancelButton, confirmButton);
    content.append(header, body, footer);
    dialog.append(content);
    modal.append(dialog);
    document.body.append(modal);

    confirmButton.addEventListener("click", confirmAiTokenEstimate);
    modal.addEventListener("hidden.bs.modal", () => {
        aiTokenEstimateState.form = null;
        aiTokenEstimateState.submitter = null;
    });

    return modal;
}

function setAiTokenEstimateLoading(modal) {
    modal.querySelector("[data-ai-token-estimate-message]").textContent = translateAiTokenEstimate(
        "Review the estimated AI usage before continuing."
    );
    modal.querySelector("[data-ai-token-estimate-context]").textContent = "";
    modal.querySelector("[data-ai-token-estimate-context]").classList.add("d-none");
    modal.querySelector("[data-ai-token-estimate-usage]").replaceChildren();
    modal.querySelector("[data-ai-token-estimate-metrics]").replaceChildren();
    modal.querySelector("[data-ai-token-estimate-details-metrics]").replaceChildren();
    modal.querySelector("[data-ai-token-estimate-details]").open = false;
    modal.querySelector("[data-ai-token-estimate-loading]").classList.remove("d-none");
    setAiTokenEstimateHidden(modal, "[data-ai-token-estimate-error]");
    setAiTokenEstimateHidden(modal, "[data-ai-token-estimate-usage]");
    setAiTokenEstimateHidden(modal, "[data-ai-token-estimate-warning]");
    setAiTokenEstimateHidden(modal, "[data-ai-token-estimate-metrics]");
    setAiTokenEstimateHidden(modal, "[data-ai-token-estimate-details]");
    modal.querySelector("[data-ai-token-estimate-confirm]").disabled = true;
}

function renderAiTokenEstimate(modal, data) {
    const estimate = data?.estimate || {};
    const metrics = modal.querySelector("[data-ai-token-estimate-metrics]");
    const details = modal.querySelector("[data-ai-token-estimate-details]");
    const detailsMetrics = modal.querySelector("[data-ai-token-estimate-details-metrics]");
    const usage = modal.querySelector("[data-ai-token-estimate-usage]");
    const warning = modal.querySelector("[data-ai-token-estimate-warning]");
    const contextStatus = aiTokenEstimateContextStatus(estimate);
    const requestCount = positiveAiTokenNumber(estimate.request_count);
    const totalTokens = positiveAiTokenNumber(estimate.total_tokens);

    modal.querySelector("[data-ai-token-estimate-loading]").classList.add("d-none");
    modal.querySelector("[data-ai-token-estimate-error]").classList.add("d-none");
    const message = modal.querySelector("[data-ai-token-estimate-message]");
    const contextMessage = modal.querySelector("[data-ai-token-estimate-context]");
    if (requestCount === 0 || totalTokens === 0) {
        message.textContent = data?.message || translateAiTokenEstimate("No AI request would be sent for this action.");
        contextMessage.textContent = "";
        contextMessage.classList.add("d-none");
    } else {
        message.textContent = translateAiTokenEstimate("This AI command is estimated to use {tokens}.", {
            tokens: formatAiTokenCount(totalTokens),
        });
        if (contextStatus) {
            contextMessage.textContent = contextStatus.message;
            contextMessage.classList.remove("d-none");
        } else {
            contextMessage.textContent = "";
            contextMessage.classList.add("d-none");
        }
    }

    usage.className = "alert ai-token-estimate-usage d-none";
    usage.replaceChildren();
    if (contextStatus) {
        const usageLabel = aiTokenEstimateElement("div", "ai-token-estimate-usage-label", contextStatus.label);
        const usageDetail = aiTokenEstimateElement(
            "div",
            "ai-token-estimate-usage-detail",
            formatAiTokenContextUsage(contextStatus.usedTokens, contextStatus.limitTokens, contextStatus.percent)
        );
        usage.classList.add(contextStatus.alertClass);
        usage.setAttribute("role", contextStatus.alertClass === "alert-danger" ? "alert" : "status");
        usage.append(usageLabel, usageDetail);
        usage.classList.remove("d-none");
    } else {
        usage.removeAttribute("role");
    }

    metrics.replaceChildren();
    detailsMetrics.replaceChildren();
    details.open = false;

    appendAiTokenEstimateRow(
        metrics,
        translateAiTokenEstimate("Estimated total tokens"),
        formatAiTokenNumber(estimate.total_tokens),
        { emphasized: true }
    );
    appendAiTokenEstimateRow(
        metrics,
        translateAiTokenEstimate("Input tokens"),
        formatAiTokenNumber(estimate.input_tokens)
    );
    appendAiTokenEstimateRow(
        metrics,
        translateAiTokenEstimate("Expected response tokens"),
        formatAiTokenNumber(estimate.expected_output_tokens)
    );
    appendAiTokenEstimateRow(
        metrics,
        translateAiTokenEstimate("AI requests"),
        formatAiTokenNumber(estimate.request_count),
        {
            helper:
                requestCount > 1
                    ? translateAiTokenEstimate("The command will be split into {count} model requests.", {
                          count: formatAiTokenNumber(requestCount),
                      })
                    : "",
        }
    );
    appendAiTokenEstimateRow(
        metrics,
        translateAiTokenEstimate("Model"),
        String(estimate.model || translateAiTokenEstimate("Unknown"))
    );
    if (contextStatus) {
        appendAiTokenEstimateRow(
            metrics,
            translateAiTokenEstimate("Context usage"),
            formatAiTokenContextUsage(contextStatus.usedTokens, contextStatus.limitTokens, contextStatus.percent)
        );
    }
    if (Object.prototype.hasOwnProperty.call(data || {}, "transaction_count")) {
        appendAiTokenEstimateRow(
            metrics,
            translateAiTokenEstimate("Records included"),
            formatAiTokenNumber(data?.transaction_count)
        );
    }

    appendAiTokenEstimateRow(
        detailsMetrics,
        translateAiTokenEstimate("Tokenizer"),
        String(estimate.tokenizer || translateAiTokenEstimate("Not available"))
    );
    appendAiTokenEstimateRow(
        detailsMetrics,
        translateAiTokenEstimate("Batches"),
        formatAiTokenNumber(estimate.batch_count)
    );
    appendAiTokenEstimateRow(
        detailsMetrics,
        translateAiTokenEstimate("Largest batch"),
        formatAiTokenCount(estimate.max_batch_total_tokens)
    );

    const warningText =
        estimate.warning ||
        (estimate.tokenizer_available === false
            ? translateAiTokenEstimate("One or more estimates are approximate.")
            : "");
    if (warningText) {
        warning.textContent = warningText;
        warning.classList.remove("d-none");
    } else {
        warning.textContent = "";
        warning.classList.add("d-none");
    }

    metrics.classList.remove("d-none");
    details.classList.remove("d-none");
    modal.querySelector("[data-ai-token-estimate-confirm]").disabled = false;
}

function renderAiTokenEstimateError(modal, message) {
    const error = modal.querySelector("[data-ai-token-estimate-error]");

    modal.querySelector("[data-ai-token-estimate-loading]").classList.add("d-none");
    setAiTokenEstimateHidden(modal, "[data-ai-token-estimate-metrics]");
    setAiTokenEstimateHidden(modal, "[data-ai-token-estimate-usage]");
    setAiTokenEstimateHidden(modal, "[data-ai-token-estimate-warning]");
    setAiTokenEstimateHidden(modal, "[data-ai-token-estimate-details]");
    modal.querySelector("[data-ai-token-estimate-context]").classList.add("d-none");
    modal.querySelector("[data-ai-token-estimate-message]").textContent =
        translateAiTokenEstimate("No estimate available.");
    error.textContent = message || translateAiTokenEstimate("Token estimate could not be loaded.");
    error.classList.remove("d-none");
    modal.querySelector("[data-ai-token-estimate-confirm]").disabled = true;
}

function aiTokenEstimateShouldHandle(form, submitter) {
    if (!form.dataset.aiTokenEstimateUrl) {
        return false;
    }

    if (form.dataset.aiTokenEstimateBypass === "true") {
        delete form.dataset.aiTokenEstimateBypass;
        return false;
    }

    return (
        form.dataset.aiTokenEstimateSubmitterRequired !== "true" ||
        Boolean(submitter?.hasAttribute?.("data-ai-token-estimate-submitter"))
    );
}

function aiTokenEstimateFormData(form, submitter) {
    const formData = new FormData(form);
    if (submitter?.name) {
        formData.append(submitter.name, submitter.value);
    }
    formData.delete(AI_TOKEN_ESTIMATE_CONFIRMED_FIELD);
    return formData;
}

function markAiTokenEstimateConfirmed(form) {
    let input = form.querySelector(`input[name="${AI_TOKEN_ESTIMATE_CONFIRMED_FIELD}"]`);
    if (!input) {
        input = document.createElement("input");
        input.type = "hidden";
        input.name = AI_TOKEN_ESTIMATE_CONFIRMED_FIELD;
        form.append(input);
    }
    input.value = "1";
}

async function requestAiTokenEstimate(form, submitter) {
    const response = await fetch(form.dataset.aiTokenEstimateUrl, {
        method: "POST",
        headers: {
            "X-CSRF-Token": getCsrfToken(),
            "X-Requested-With": "fetch",
        },
        body: aiTokenEstimateFormData(form, submitter),
        credentials: "same-origin",
    });
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : {};
    if (!response.ok || data.ok === false) {
        throw new Error(data.message || translateAiTokenEstimate("Token estimate could not be loaded."));
    }
    return data;
}

async function handleAiTokenEstimateSubmit(event) {
    const form = event.currentTarget;
    const submitter = event.submitter || null;

    if (!aiTokenEstimateShouldHandle(form, submitter)) {
        return;
    }
    if (!form.reportValidity()) {
        return;
    }

    event.preventDefault();
    event.stopImmediatePropagation();

    const modalElement = ensureAiTokenEstimateModal();
    const modal = window.bootstrap?.Modal ? window.bootstrap.Modal.getOrCreateInstance(modalElement) : null;
    aiTokenEstimateState.form = form;
    aiTokenEstimateState.submitter = submitter;
    aiTokenEstimateState.modal = modal;
    setAiTokenEstimateLoading(modalElement);
    modal?.show();

    try {
        const data = await requestAiTokenEstimate(form, submitter);
        renderAiTokenEstimate(modalElement, data);
    } catch (error) {
        renderAiTokenEstimateError(modalElement, error?.message || "");
    }
}

function confirmAiTokenEstimate() {
    const form = aiTokenEstimateState.form;
    const submitter = aiTokenEstimateState.submitter;

    if (!form || !document.body.contains(form)) {
        return;
    }

    markAiTokenEstimateConfirmed(form);
    form.dataset.aiTokenEstimateBypass = "true";
    aiTokenEstimateState.modal?.hide();

    if (form.requestSubmit) {
        form.requestSubmit(submitter && document.body.contains(submitter) ? submitter : undefined);
    } else {
        form.submit();
    }
}

function setupAiTokenEstimateForms(root = document) {
    root.querySelectorAll(AI_TOKEN_ESTIMATE_FORM_SELECTOR).forEach((form) => {
        if (form.dataset.aiTokenEstimateReady === "true") {
            return;
        }

        form.dataset.aiTokenEstimateReady = "true";
        form.addEventListener("submit", handleAiTokenEstimateSubmit, { capture: true });
    });
}

window.financeApp?.registerInitializer("ai-token-estimate.forms", setupAiTokenEstimateForms);

setupAiTokenEstimateForms();
