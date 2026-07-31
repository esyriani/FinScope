const JOBS_REFRESH_DEFAULT_SECONDS = 10;

function translateJobsMessage(message, variables) {
    // Return a translated browser message when the shared helper is available.
    return window.financeTranslate ? window.financeTranslate(message, variables) : message;
}

function aiLogIconClass(level) {
    if (level === "error") {
        return "bi bi-x-circle-fill text-danger flex-shrink-0";
    }
    if (level === "warning") {
        return "bi bi-exclamation-triangle-fill text-warning flex-shrink-0";
    }
    return "bi bi-info-circle text-muted flex-shrink-0";
}

function setupAiJobProgressPolling(root = document) {
    const panels = Array.from(root.querySelectorAll("[data-ai-job-progress]"));

    panels.forEach((panel) => {
        if (panel.dataset.aiJobProgressReady === "true") {
            return;
        }

        panel.dataset.aiJobProgressReady = "true";
        const statusUrl = panel.dataset.jobStatusUrl;
        const bar = panel.querySelector("[data-ai-job-progress-bar]");
        const percentLabel = panel.querySelector("[data-ai-job-progress-percent]");
        const summary = panel.querySelector("[data-ai-job-progress-summary]");
        const log = panel.querySelector("[data-ai-job-progress-log]");
        const progress = panel.querySelector(".progress");
        let stopped = false;

        if (!statusUrl || !bar || !percentLabel || !summary || !progress) {
            return;
        }

        function boundedPercent(value) {
            const percent = Number(value || 0);
            if (!Number.isFinite(percent)) {
                return 0;
            }
            return Math.min(Math.max(Math.round(percent), 0), 100);
        }

        function renderProgress(data) {
            const percent = boundedPercent(data.progress_percent);
            bar.style.width = `${percent}%`;
            progress.setAttribute("aria-valuenow", String(percent));
            percentLabel.textContent = translateJobsMessage("{percent}% complete", { percent });
            summary.textContent = data.progress_message
                ? translateJobsMessage(data.progress_message, data.progress_params || {})
                : translateJobsMessage("Waiting for progress update.");
            renderLog(data.progress_log || []);
        }

        function renderLog(entries) {
            if (!log) {
                return;
            }

            if (!entries.length) {
                const empty = document.createElement("div");
                empty.className = "text-muted";
                empty.dataset.aiJobProgressLogEmpty = "";
                empty.textContent = translateJobsMessage("No log entries yet.");
                log.replaceChildren(empty);
                return;
            }

            log.replaceChildren(
                ...entries.map((entry) => {
                    const level = String(entry.level || "info").toLowerCase();
                    const row = document.createElement("div");
                    row.className = "d-flex align-items-start gap-2 py-1";
                    row.dataset.aiJobProgressLogEntry = "";

                    const timestamp = document.createElement("span");
                    timestamp.className = "text-muted flex-shrink-0";
                    timestamp.textContent = entry.timestamp_label || "";

                    const icon = document.createElement("i");
                    icon.className = aiLogIconClass(level);
                    icon.setAttribute("aria-hidden", "true");

                    const message = document.createElement("span");
                    message.textContent = translateJobsMessage(entry.message || "", entry.params || {});

                    row.append(timestamp, icon, message);
                    return row;
                })
            );
        }

        function refreshJobsSection() {
            const target = panel.closest("[data-ajax-refresh-target]");
            const key = target?.dataset.ajaxRefreshTarget || "";
            const escapedKey = window.CSS?.escape ? CSS.escape(key) : key.replaceAll('"', '\\"');
            const selector = target?.id ? `#${target.id}` : key ? `[data-ajax-refresh-target="${escapedKey}"]` : "";

            if (selector && window.ajaxRefreshFromUrl) {
                window.ajaxRefreshFromUrl(window.location.href, selector).catch(() => {});
            }
        }

        async function poll() {
            if (stopped || !document.body.contains(panel)) {
                stopped = true;
                return;
            }

            try {
                const response = await fetch(statusUrl, {
                    method: "GET",
                    headers: {
                        "X-Requested-With": "fetch",
                    },
                    credentials: "same-origin",
                });
                if (!response.ok) {
                    return;
                }

                const data = await response.json();
                renderProgress(data);

                if (data.status !== "running") {
                    stopped = true;
                    window.setTimeout(refreshJobsSection, 500);
                }
            } catch (_error) {
                summary.textContent = translateJobsMessage("The processing progress could not be refreshed.");
            }
        }

        if (panel.dataset.jobStatus === "running") {
            const intervalId = window.setInterval(() => {
                if (stopped || !document.body.contains(panel)) {
                    window.clearInterval(intervalId);
                    return;
                }
                poll();
            }, 4000);
            poll();
        }
    });
}

function captureJobsRefreshState(selector) {
    // Preserve the user's expanded progress rows across table replacement.
    const target = document.querySelector(selector);
    const expandedRows = {};

    target?.querySelectorAll("tr[id^='job-progress-']").forEach((row) => {
        expandedRows[row.id] = row.classList.contains("show");
    });

    return { expandedRows };
}

function restoreJobsRefreshState(state) {
    // Restore only rows that still exist after the refresh.
    Object.entries(state.expandedRows || {}).forEach(([id, expanded]) => {
        const row = document.getElementById(id);
        if (!row) {
            return;
        }

        const escapedId = window.CSS?.escape ? CSS.escape(id) : id.replaceAll('"', '\\"');
        row.classList.toggle("show", expanded);
        document.querySelectorAll(`[data-bs-target="#${escapedId}"]`).forEach((toggle) => {
            toggle.setAttribute("aria-expanded", String(expanded));
        });
    });
}

function jobsRefreshIsBlocked(selector) {
    // Do not replace the table while a modal or form action is in progress.
    const target = document.querySelector(selector);
    return Boolean(
        document.querySelector(".modal.show") ||
        document.querySelector("[data-ajax-refresh-form][aria-busy='true']") ||
        target?.getAttribute("aria-busy") === "true"
    );
}

function renderJobsRefreshCountdown(button, seconds) {
    // Keep the visible refresh label synchronized with the timer.
    const label = button.querySelector("[data-jobs-refresh-label]");
    const text = translateJobsMessage("Refresh ({seconds})", { seconds });

    if (label) {
        label.textContent = text;
    } else {
        button.textContent = text;
    }
}

function setupJobsAutoRefresh(root = document) {
    // Refresh the jobs table through AJAX on a ten-second countdown.
    const button = document.querySelector("[data-jobs-refresh-button]");
    const target = root.querySelector("[data-jobs-auto-refresh]") || document.querySelector("[data-jobs-auto-refresh]");

    if (!button || !target || button.dataset.jobsRefreshReady === "true") {
        return;
    }

    button.dataset.jobsRefreshReady = "true";
    const selector = button.dataset.jobsRefreshTarget || '[data-ajax-refresh-target="jobs-actions"]';
    const configuredSeconds = Number(
        button.dataset.jobsRefreshInterval || target.dataset.jobsAutoRefreshInterval || JOBS_REFRESH_DEFAULT_SECONDS
    );
    const intervalSeconds =
        Number.isFinite(configuredSeconds) && configuredSeconds > 0
            ? Math.round(configuredSeconds)
            : JOBS_REFRESH_DEFAULT_SECONDS;
    let remainingSeconds = intervalSeconds;
    let refreshing = false;

    function resetCountdown() {
        remainingSeconds = intervalSeconds;
        renderJobsRefreshCountdown(button, remainingSeconds);
    }

    async function refreshNow() {
        if (refreshing || !window.ajaxRefreshFromUrl) {
            return;
        }

        if (jobsRefreshIsBlocked(selector)) {
            resetCountdown();
            return;
        }

        const state = captureJobsRefreshState(selector);
        const activeTarget = document.querySelector(selector);
        refreshing = true;
        button.setAttribute("aria-disabled", "true");
        activeTarget?.setAttribute("aria-busy", "true");

        try {
            await window.ajaxRefreshFromUrl(window.location.href, selector);
            restoreJobsRefreshState(state);
        } catch (_error) {
            if (window.showAjaxRefreshError) {
                window.showAjaxRefreshError(
                    activeTarget || button,
                    selector,
                    translateJobsMessage("The processing table could not be refreshed.")
                );
            }
        } finally {
            refreshing = false;
            button.removeAttribute("aria-disabled");
            document.querySelector(selector)?.removeAttribute("aria-busy");
            resetCountdown();
        }
    }

    button.addEventListener("click", (event) => {
        if (!window.ajaxRefreshFromUrl) {
            return;
        }

        event.preventDefault();
        refreshNow();
    });

    resetCountdown();
    const intervalId = window.setInterval(() => {
        if (!document.body.contains(button)) {
            window.clearInterval(intervalId);
            return;
        }

        remainingSeconds -= 1;
        if (remainingSeconds <= 0) {
            refreshNow();
        } else {
            renderJobsRefreshCountdown(button, remainingSeconds);
        }
    }, 1000);
}

window.financeApp?.registerInitializer("jobs.auto-refresh", setupJobsAutoRefresh);
window.financeApp?.registerInitializer("jobs.ai-progress-polling", setupAiJobProgressPolling);

setupJobsAutoRefresh();
setupAiJobProgressPolling();
