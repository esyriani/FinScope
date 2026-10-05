(function () {
    function bootstrapFormMessage(form) {
        return form.dataset.passwordMismatchMessage || "Passwords do not match.";
    }

    function shakePanel(panel) {
        if (!panel) {
            return;
        }
        panel.classList.remove("auth-panel-shake");
        void panel.offsetWidth;
        panel.classList.add("auth-panel-shake");
    }

    function clearPasswordMismatch(passwordInput, confirmInput) {
        passwordInput.classList.remove("is-invalid");
        confirmInput.classList.remove("is-invalid");
        passwordInput.removeAttribute("aria-invalid");
        confirmInput.removeAttribute("aria-invalid");
        confirmInput.setCustomValidity("");
    }

    function showPasswordMismatch(form, passwordInput, confirmInput) {
        const message = bootstrapFormMessage(form);
        const feedback = form.querySelector("[data-password-match-feedback]");

        if (feedback) {
            feedback.textContent = message;
        }
        passwordInput.classList.add("is-invalid");
        confirmInput.classList.add("is-invalid");
        passwordInput.setAttribute("aria-invalid", "true");
        confirmInput.setAttribute("aria-invalid", "true");
        confirmInput.setCustomValidity(message);
        form.classList.add("was-validated");
        shakePanel(form.closest("[data-auth-bootstrap-panel]"));
        confirmInput.focus();
    }

    function setupBootstrapForm(root = document) {
        root.querySelectorAll("[data-auth-bootstrap-form]").forEach((form) => {
            if (form.dataset.authBootstrapReady === "true") {
                return;
            }

            const passwordInput = form.querySelector("input[name='password']");
            const confirmInput = form.querySelector("input[name='confirm_password']");
            if (!passwordInput || !confirmInput) {
                return;
            }

            form.dataset.authBootstrapReady = "true";
            form.addEventListener("submit", (event) => {
                if (passwordInput.value === confirmInput.value) {
                    clearPasswordMismatch(passwordInput, confirmInput);
                    return;
                }

                event.preventDefault();
                event.stopPropagation();
                showPasswordMismatch(form, passwordInput, confirmInput);
            });

            [passwordInput, confirmInput].forEach((input) => {
                input.addEventListener("input", () => {
                    if (passwordInput.value === confirmInput.value) {
                        clearPasswordMismatch(passwordInput, confirmInput);
                    }
                });
            });
        });
    }

    window.financeApp?.registerInitializer("auth.bootstrap", setupBootstrapForm);
    setupBootstrapForm();
})();
