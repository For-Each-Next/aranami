"use strict";

// Enhance each newly displayed draft without changing its literal source text.
function initializeNewsletterResult(root = document) {
    for (const button of root.querySelectorAll("[data-newsletter-copy]")) {
        const source = document.getElementById(button.dataset.newsletterCopy);
        const status = button.parentElement.querySelector("[data-newsletter-status]");
        if (!source || !status) continue;
        button.hidden = false;
        button.addEventListener("click", async () => {
            status.textContent = "";
            button.disabled = true;
            try {
                if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
                await navigator.clipboard.writeText(source.textContent);
                status.textContent = button.dataset.copySuccess;
            } catch {
                source.parentElement.focus();
                const range = document.createRange();
                range.selectNodeContents(source);
                const selection = document.getSelection();
                selection?.removeAllRanges();
                selection?.addRange(range);
                status.textContent = button.dataset.copyFailure;
            } finally {
                button.disabled = false;
            }
        });
    }
}

// Keep the issue menu stable while replacing only its saved draft and dates.
function initializeIssueForm() {
    if (typeof fetch !== "function" || typeof DOMParser !== "function" || typeof AbortController !== "function") return;
    const form = document.querySelector("[data-newsletter-issue-form]");
    if (!form) return;
    const issue = form.querySelector('[name="issue"]');
    const submit = form.querySelector('button[type="submit"]');
    const status = document.querySelector("[data-newsletter-load-status]");
    let result = document.querySelector("[data-newsletter-result]");
    let displayedIssue = issue.value;
    let requestNumber = 0;
    let controller;
    submit.hidden = true;

    async function loadIssue() {
        const request = ++requestNumber;
        controller?.abort();
        controller = new AbortController();
        const requestedIssue = issue.value;
        const url = new URL(form.action, location.href);
        url.search = new URLSearchParams(new FormData(form)).toString();
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
            const replacement = page.querySelector("[data-newsletter-result]");
            if (!replacement || page.querySelector('#newsletter-issue')?.value !== requestedIssue) {
                throw new Error("Invalid newsletter response");
            }
            result.replaceWith(replacement);
            result = replacement;
            for (const selector of [".contents-desktop > ul", ".contents-mobile .page-toc > ul"]) {
                const links = page.querySelector(selector);
                if (links) document.querySelector(selector)?.replaceWith(links);
            }
            for (const link of document.querySelectorAll(".language-switcher a[lang]")) {
                const replacementLink = page.querySelector(`.language-switcher a[lang="${link.lang}"]`);
                if (replacementLink) link.setAttribute("href", replacementLink.getAttribute("href"));
            }
            displayedIssue = requestedIssue;
            history.replaceState(null, "", url);
            initializeNewsletterResult(result);
        } catch {
            if (request !== requestNumber) return;
            issue.value = displayedIssue;
            status.textContent = status.dataset.failure;
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
