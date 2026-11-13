async function copySource(button) {
    const source = document.getElementById(button.dataset.copyTarget);
    const status = button
        .closest(".newsletter-copy-controls")
        ?.querySelector("[data-copy-status]");
    if (!(source instanceof HTMLTextAreaElement) || !status) return;
    status.textContent = "";
    button.disabled = true;
    try {
        if (!navigator.clipboard?.writeText)
            throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(source.value);
        status.textContent = button.dataset.copySuccess;
    } catch {
        source.focus();
        source.select();
        status.textContent = button.dataset.copyFailure;
    } finally {
        button.disabled = false;
    }
}
function initializeCopyControls() {
    if (document.documentElement.hasAttribute("data-copy-controls")) return;
    document.documentElement.setAttribute("data-copy-controls", "");
    document.addEventListener("click", (event) => {
        if (!(event.target instanceof Element)) return;
        const button = event.target.closest("button[data-copy-target]");
        if (button && !button.disabled) void copySource(button);
    });
}
export { initializeCopyControls };
