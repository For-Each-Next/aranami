"use strict";

// Copy the editor's current draft; no content leaves this page.
for (const button of document.querySelectorAll("[data-newsletter-copy]")) {
    const editor = document.getElementById(button.dataset.newsletterCopy);
    const status = button.parentElement.querySelector("[data-newsletter-status]");
    if (!editor || !status) continue;
    button.hidden = false;
    button.addEventListener("click", async () => {
        status.textContent = "";
        button.disabled = true;
        try {
            if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
            await navigator.clipboard.writeText(editor.value);
            status.textContent = button.dataset.copySuccess;
        } catch {
            editor.focus();
            editor.select();
            status.textContent = button.dataset.copyFailure;
        } finally {
            button.disabled = false;
        }
    });
}
