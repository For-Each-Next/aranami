"use strict";

// Native field sizing follows wrapped text; rows keep older browsers usable.
function sizeNewsletterText(source: HTMLTextAreaElement): void {
    if (globalThis.CSS?.supports("field-sizing", "content") || !source.closest("details")?.open) return;
    source.rows = 1;
    const style = getComputedStyle(source);
    const lineHeight = parseFloat(style.lineHeight);
    const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    source.rows = Math.max(4, Math.ceil((source.scrollHeight - padding) / lineHeight));
}

// Enhance each newly displayed draft without changing its literal source text.
function initializeNewsletterResult(root: ParentNode = document): void {
    for (const source of root.querySelectorAll<HTMLTextAreaElement>("[data-newsletter-wikitext]")) {
        source.closest("details")?.addEventListener("toggle", () => sizeNewsletterText(source));
        sizeNewsletterText(source);
    }
    for (const button of root.querySelectorAll<HTMLButtonElement>("[data-newsletter-copy]")) {
        const source = document.getElementById(button.dataset.newsletterCopy!) as HTMLTextAreaElement | null;
        const controls = button.closest?.(".newsletter-copy-controls") ?? button.parentElement;
        const status = controls?.querySelector<HTMLElement>("[data-newsletter-status]");
        if (!source || !status) continue;
        button.addEventListener("click", async () => {
            status.textContent = "";
            button.disabled = true;
            try {
                if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
                await navigator.clipboard.writeText(source.value);
                status.textContent = button.dataset.copySuccess!;
            } catch {
                const accordion = source.closest("details");
                if (accordion) accordion.open = true;
                sizeNewsletterText(source);
                source.focus();
                source.select();
                status.textContent = button.dataset.copyFailure!;
            } finally {
                button.disabled = false;
            }
        });
    }
}

// Keep the issue menu stable while replacing only its saved draft and dates.
function initializeIssueForm(): void {
    if (typeof fetch !== "function" || typeof DOMParser !== "function" || typeof AbortController !== "function") return;
    const form = document.querySelector<HTMLFormElement>("[data-newsletter-issue-form]");
    if (!form) return;
    const issue = form.querySelector<HTMLSelectElement>('[name="issue"]')!;
    const status = document.querySelector<HTMLElement>("[data-newsletter-load-status]")!;
    const statusText = status.querySelector<HTMLElement>(".cdx-message__content")!;
    let result = document.querySelector<HTMLElement>("[data-newsletter-result]")!;
    let displayedIssue = issue.value;
    let requestNumber = 0;
    let controller: AbortController | undefined;

    async function loadIssue(): Promise<void> {
        const request = ++requestNumber;
        controller?.abort();
        controller = new AbortController();
        const requestedIssue = issue.value;
        const url = new URL(form!.action, location.href);
        // This form contains only textual issue and language controls.
        url.search = new URLSearchParams([...new FormData(form!)] as [string, string][]).toString();
        status.hidden = true;
        result.setAttribute("aria-busy", "true");
        try {
            if (url.origin !== location.origin) throw new Error("Unexpected newsletter origin");
            const response = await fetch(url, {
                signal: controller.signal,
                credentials: "same-origin",
                redirect: "error",
                headers: {Accept: "text/html"},
            });
            if (!response.ok) throw new Error("Newsletter unavailable");
            const page = new DOMParser().parseFromString(await response.text(), "text/html");
            if (request !== requestNumber) return;
            const replacement = page.querySelector<HTMLElement>("[data-newsletter-result]");
            if (!replacement || page.querySelector<HTMLSelectElement>('#newsletter-issue')?.value !== requestedIssue) {
                throw new Error("Invalid newsletter response");
            }
            globalThis.aranamiComponents?.dispose(result);
            result.replaceWith(replacement);
            result = replacement;
            globalThis.aranamiComponents?.enhance(result);
            for (const selector of [".contents-desktop > ul", ".contents-mobile .page-toc > ul"]) {
                const links = page.querySelector(selector);
                if (links) document.querySelector(selector)?.replaceWith(links);
            }
            for (const link of document.querySelectorAll<HTMLAnchorElement>(".language-switcher a[lang]")) {
                const replacementLink = page.querySelector<HTMLAnchorElement>(`.language-switcher a[lang="${link.lang}"]`);
                if (replacementLink) link.setAttribute("href", replacementLink.getAttribute("href")!);
            }
            displayedIssue = requestedIssue;
            history.replaceState(null, "", url);
            initializeNewsletterResult(result);
        } catch {
            if (request !== requestNumber) return;
            issue.value = displayedIssue;
            statusText.textContent = status.dataset.failure!;
            status.hidden = false;
        } finally {
            if (request === requestNumber) result.removeAttribute("aria-busy");
        }
    }
    issue.addEventListener("change", loadIssue);
    form.addEventListener("submit", event => {
        event.preventDefault();
        loadIssue();
    });
}

initializeNewsletterResult();
initializeIssueForm();
globalThis.addEventListener("resize", () => {
    for (const source of document.querySelectorAll<HTMLTextAreaElement>("[data-newsletter-wikitext]")) {
        sizeNewsletterText(source);
    }
});
