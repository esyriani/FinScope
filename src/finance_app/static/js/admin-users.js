function temporaryPasswordCopyButtons(root) {
    return [
        ...(root.matches?.("[data-copy-temporary-password]") ? [root] : []),
        ...Array.from(root.querySelectorAll("[data-copy-temporary-password]")),
    ];
}

function setupTemporaryPasswordCopy(root = document) {
    const copyButtons = temporaryPasswordCopyButtons(root);

    copyButtons.forEach((copyButton) => {
        if (copyButton.dataset.copyTemporaryPasswordReady === "true") {
            return;
        }

        const modal = copyButton.closest(".modal") || document;
        const passwordInput = modal.querySelector("[data-temporary-password]");
        if (!passwordInput || !navigator.clipboard) {
            return;
        }

        copyButton.dataset.copyTemporaryPasswordReady = "true";
        copyButton.addEventListener("click", () => {
            navigator.clipboard.writeText(passwordInput.value);
        });
    });
}

window.financeApp?.registerInitializer("admin-users.temporary-password-copy", setupTemporaryPasswordCopy);

setupTemporaryPasswordCopy();
