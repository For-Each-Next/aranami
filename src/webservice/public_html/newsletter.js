"use strict";

// Color common wikitext constructs without interpreting source as HTML.
function highlightWikitext(source) {
    const text = source.textContent;
    const fragment = document.createDocumentFragment();
    const syntax = /<!--[\s\S]*?(?:-->|$)|\[\[[^\n]*?(?:\]\]|$)|\{\{\{?[^{}\n|]*|\}\}\}?|^={2,6}[^\n]*={2,6}[ \t]*$|<\/?[A-Za-z][^>\n]*>|'{2,5}|[|=]/gm;
    let previous = 0;
    for (const match of text.matchAll(syntax)) {
        fragment.append(document.createTextNode(text.slice(previous, match.index)));
        const token = document.createElement("span");
        const value = match[0];
        let kind = "delimiter";
        if (value.startsWith("<!--")) kind = "comment";
        else if (value.startsWith("[[")) kind = "link";
        else if (value.startsWith("{{") || value.startsWith("}}")) kind = "template";
        else if (value.startsWith("<")) kind = "tag";
        else if (value.startsWith("'")) kind = "emphasis";
        else if (value.startsWith("==")) kind = "heading";
        token.className = `newsletter-token-${kind}`;
        token.textContent = value;
        fragment.append(token);
        previous = match.index + value.length;
    }
    fragment.append(document.createTextNode(text.slice(previous)));
    source.replaceChildren(fragment);
}

for (const source of document.querySelectorAll("[data-newsletter-wikitext]")) {
    highlightWikitext(source);
}

// Issue changes navigate to saved drafts through the native read-only form.
const issueForm = document.querySelector("[data-newsletter-issue-form]");
if (issueForm) {
    for (const radio of issueForm.querySelectorAll('input[name="issue"]')) {
        radio.addEventListener("change", () => {
            if (radio.checked) issueForm.requestSubmit();
        });
    }
    issueForm.hidden = false;
}

// Copy literal wikitext, preserving the same text beneath the highlighting.
for (const button of document.querySelectorAll("[data-newsletter-copy]")) {
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
