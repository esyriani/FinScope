function flatpickrAltInputClass(input) {
    const classes = ["form-control"];
    if (input.classList.contains("form-control-sm")) {
        classes.push("form-control-sm");
    }
    return classes.join(" ");
}

function submitFlatpickrForm(input) {
    if (!input.hasAttribute("data-flatpickr-submit-on-change")) return;
    const form = input.form;
    if (!form) return;
    if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
        return;
    }
    form.submit();
}

function setupFlatpickrInputs(root = document) {
    if (!window.flatpickr) return;

    root.querySelectorAll("[data-flatpickr-date]").forEach((input) => {
        if (input.dataset.flatpickrReady === "true") return;
        input.dataset.flatpickrReady = "true";
        input.financeFlatpickr = flatpickr(input, {
            allowInput: true,
            altFormat: "d-M-Y",
            altInput: true,
            altInputClass: flatpickrAltInputClass(input),
            dateFormat: "Y-m-d",
            disableMobile: true,
            position: "auto center",
        });
    });

    root.querySelectorAll("[data-flatpickr-month]").forEach((input) => {
        if (input.dataset.flatpickrReady === "true") return;
        input.dataset.flatpickrReady = "true";
        const monthPlugins = [];
        if (window.monthSelectPlugin) {
            monthPlugins.push(
                new monthSelectPlugin({
                    altFormat: "F Y",
                    dateFormat: "Y-m",
                })
            );
        }
        input.financeFlatpickr = flatpickr(input, {
            allowInput: true,
            altFormat: "F Y",
            altInput: true,
            altInputClass: flatpickrAltInputClass(input),
            dateFormat: "Y-m",
            defaultDate: input.value || null,
            disableMobile: true,
            onChange: () => submitFlatpickrForm(input),
            plugins: monthPlugins,
            position: "auto center",
        });
    });
}

window.financeApp?.registerInitializer("dates.flatpickr", setupFlatpickrInputs);

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setupFlatpickrInputs());
} else {
    setupFlatpickrInputs();
}
