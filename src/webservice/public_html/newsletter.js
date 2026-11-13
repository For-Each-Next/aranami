function sizeNewsletterText(source) {
    if (
        globalThis.CSS?.supports("field-sizing", "content") ||
        !source.closest("details")?.open
    )
        return;
    source.rows = 1;
    const style = getComputedStyle(source);
    const lineHeight = parseFloat(style.lineHeight);
    const padding =
        parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    source.rows = Math.max(
        4,
        Math.ceil((source.scrollHeight - padding) / lineHeight),
    );
}
function initializeNewsletterResult(root = document) {
    for (const date of root.querySelectorAll("#newsletter-data-date")) {
        date.addEventListener("change", () => {
            if (date.value) date.form?.requestSubmit();
        });
    }
    for (const source of root.querySelectorAll("[data-newsletter-wikitext]")) {
        source
            .closest("details")
            ?.addEventListener("toggle", () => sizeNewsletterText(source));
        sizeNewsletterText(source);
    }
    for (const button of root.querySelectorAll("[data-newsletter-copy]")) {
        const source = document.getElementById(button.dataset.newsletterCopy);
        const controls =
            button.closest?.(".newsletter-copy-controls") ??
            button.parentElement;
        const status = controls?.querySelector("[data-newsletter-status]");
        if (!source || !status) continue;
        button.addEventListener("click", async () => {
            status.textContent = "";
            button.disabled = true;
            try {
                if (!navigator.clipboard?.writeText)
                    throw new Error("Clipboard unavailable");
                await navigator.clipboard.writeText(source.value);
                status.textContent = button.dataset.copySuccess;
            } catch {
                const accordion = source.closest("details");
                if (accordion) accordion.open = true;
                sizeNewsletterText(source);
                source.focus();
                source.select();
                status.textContent = button.dataset.copyFailure;
            } finally {
                button.disabled = false;
            }
        });
    }
}
function initializeIssueForm() {
    const form = document.querySelector("[data-newsletter-issue-form]");
    if (!form) return;
    const issue = form.querySelector('[name="issue"]');
    if (
        typeof fetch !== "function" ||
        typeof DOMParser !== "function" ||
        typeof AbortController !== "function"
    ) {
        issue.addEventListener("change", () => form.requestSubmit());
        return;
    }
    const status = document.querySelector("[data-newsletter-load-status]");
    const statusText = status.querySelector(".ui-message__content");
    let result = document.querySelector("[data-newsletter-result]");
    let displayedIssue = issue.value;
    let requestNumber = 0;
    let controller;
    async function loadIssue() {
        const selected = issue.selectedOptions[0];
        if (!selected || selected.disabled) return;
        const request = ++requestNumber;
        controller?.abort();
        controller = new AbortController();
        const requestedIssue = issue.value;
        const url = new URL(form.action, location.href);
        url.search = new URLSearchParams([...new FormData(form)]).toString();
        status.hidden = true;
        result.setAttribute("aria-busy", "true");
        try {
            if (url.origin !== location.origin)
                throw new Error("Unexpected newsletter origin");
            const response = await fetch(url, {
                signal: controller.signal,
                credentials: "same-origin",
                redirect: "error",
                headers: { Accept: "text/html" },
            });
            if (!response.ok) throw new Error("Newsletter unavailable");
            const page = new DOMParser().parseFromString(
                await response.text(),
                "text/html",
            );
            if (request !== requestNumber) return;
            const replacement = page.querySelector("[data-newsletter-result]");
            const choices = page.querySelector("#newsletter-issue");
            if (!replacement || !choices || choices.value !== requestedIssue) {
                throw new Error("Invalid newsletter response");
            }
            result.replaceWith(replacement);
            result = replacement;
            globalThis.aranamiComponents?.enhance(result);
            issue.replaceChildren(...choices.options);
            issue.value = requestedIssue;
            for (const selector of [
                ".contents-desktop > ul",
                ".contents-mobile .page-toc > ul",
            ]) {
                const links = page.querySelector(selector);
                if (links)
                    document.querySelector(selector)?.replaceWith(links);
            }
            for (const link of document.querySelectorAll(
                ".language-switcher a[lang]",
            )) {
                const replacementLink = page.querySelector(
                    `.language-switcher a[lang="${link.lang}"]`,
                );
                if (replacementLink)
                    link.setAttribute(
                        "href",
                        replacementLink.getAttribute("href"),
                    );
            }
            displayedIssue = requestedIssue;
            history.replaceState(null, "", url);
            initializeNewsletterResult(result);
        } catch {
            if (request !== requestNumber) return;
            issue.value = displayedIssue;
            statusText.textContent = status.dataset.failure;
            status.hidden = false;
        } finally {
            if (request === requestNumber) result.removeAttribute("aria-busy");
        }
    }
    issue.addEventListener("change", loadIssue);
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        loadIssue();
    });
}
initializeNewsletterResult();
initializeIssueForm();
globalThis.addEventListener("resize", () => {
    for (const source of document.querySelectorAll(
        "[data-newsletter-wikitext]",
    )) {
        sizeNewsletterText(source);
    }
});
