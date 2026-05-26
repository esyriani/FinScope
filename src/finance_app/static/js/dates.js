function setupFlatpickrInputs() {
    if (!window.flatpickr) return;

    document.querySelectorAll("[data-flatpickr-date]").forEach((input) => {
        if (input.dataset.flatpickrReady === "true") return;
        input.dataset.flatpickrReady = "true";
        flatpickr(input, {
            allowInput: true,
            altFormat: "d-M-Y",
            altInput: true,
            altInputClass: "form-control",
            dateFormat: "Y-m-d",
            disableMobile: true,
            position: "auto center"
        });
    });

    document.querySelectorAll("[data-flatpickr-month]").forEach((input) => {
        if (input.dataset.flatpickrReady === "true") return;
        input.dataset.flatpickrReady = "true";
        const monthPlugins = [];
        if (window.monthSelectPlugin) {
            monthPlugins.push(
                new monthSelectPlugin({
                    altFormat: "F Y",
                    dateFormat: "Y-m"
                })
            );
        }
        flatpickr(input, {
            allowInput: true,
            altFormat: "F Y",
            altInput: true,
            altInputClass: "form-control",
            dateFormat: "Y-m",
            disableMobile: true,
            plugins: monthPlugins,
            position: "auto center"
        });
    });
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupFlatpickrInputs);
} else {
    setupFlatpickrInputs();
}

window.setupFlatpickrInputs = setupFlatpickrInputs;
