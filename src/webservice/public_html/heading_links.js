const stableFragment = /^[a-z][a-z0-9-]*$/;
const eligibleHeading = "main .page-heading h1, main h2[id]";
function pathSegment(segment) {
    let decoded = segment;
    try {
        decoded = decodeURIComponent(segment);
    } catch {}
    return encodeURIComponent(decoded).replace(
        /[!'()*]/g,
        (character) =>
            `%${character.charCodeAt(0).toString(16).toUpperCase()}`,
    );
}
function linkAlias(label) {
    const entities = {
        "&": "&amp;",
        "[": "&#91;",
        "]": "&#93;",
        "|": "&#124;",
        "<": "&lt;",
        ">": "&gt;",
        "{": "&#123;",
        "}": "&#125;",
        "'": "&#39;",
    };
    return label
        .replace(/\s+/g, " ")
        .trim()
        .replace(/[&[\]|<>{}']/g, (character) => entities[character]);
}
function headingWikilink(pathname, fragment, label, search = "") {
    if (!stableFragment.test(fragment))
        throw new Error("A section link requires a stable local route.");
    return `[[toolforge:aranami${localRoute(pathname, search)}#${fragment}|${linkAlias(label)}]]`;
}
function localRoute(pathname, search) {
    if (!pathname.startsWith("/") || pathname.startsWith("//"))
        throw new Error("A heading link requires a stable local route.");
    const route = pathname.split("/").map(pathSegment).join("/");
    const parameters = new URLSearchParams(search);
    parameters.delete("lang");
    const query = parameters.toString();
    return `${route}${query ? `?${query}` : ""}`;
}
function pageWikilink(pathname, label, search = "") {
    return `[[toolforge:aranami${localRoute(pathname, search)}|${linkAlias(label)}]]`;
}
function initializeHeadingLinks() {
    const messages = document.body.dataset;
    if (
        document.documentElement.hasAttribute("data-heading-links") ||
        !messages.headingCopyLabel ||
        !messages.headingCopySuccess ||
        !messages.headingCopyFailure ||
        !messages.headingPermalinkLabel ||
        !messages.headingPagePermalinkLabel ||
        !messages.headingCopyClose
    )
        return;
    document.documentElement.setAttribute("data-heading-links", "");
    const announcement = document.createElement("p");
    announcement.className = "visually-hidden";
    announcement.setAttribute("role", "status");
    announcement.setAttribute("aria-live", "polite");
    document.body.append(announcement);
    let manualDialog;
    let lastOpener;
    const selectManually = (source, control, page) => {
        manualDialog?.remove();
        manualDialog = document.createElement("dialog");
        manualDialog.className = "heading-copy-dialog";
        manualDialog.setAttribute("aria-labelledby", "heading-copy-title");
        const title = document.createElement("p");
        title.id = "heading-copy-title";
        title.textContent = messages.headingCopyFailure;
        const input = document.createElement("input");
        input.type = "text";
        input.readOnly = true;
        input.value = source;
        input.setAttribute("aria-label", control.getAttribute("aria-label"));
        const permalink = document.createElement("a");
        permalink.href = control.getAttribute("href");
        permalink.textContent = page
            ? messages.headingPagePermalinkLabel
            : messages.headingPermalinkLabel;
        permalink.addEventListener("click", () => manualDialog?.close());
        const close = document.createElement("button");
        close.type = "button";
        close.textContent = messages.headingCopyClose;
        close.addEventListener("click", () => manualDialog?.close());
        const actions = document.createElement("div");
        actions.className = "heading-copy-actions";
        actions.append(permalink, close);
        manualDialog.append(title, input, actions);
        lastOpener = control;
        manualDialog.addEventListener("close", () => {
            if (lastOpener?.isConnected) lastOpener.focus();
        });
        document.body.append(manualDialog);
        manualDialog.showModal();
        input.focus();
        input.select();
    };
    const enhance = (root) => {
        const headings = [...root.querySelectorAll("h1, h2[id]")];
        if (root instanceof HTMLHeadingElement) headings.unshift(root);
        for (const heading of headings) {
            if (
                !heading.matches(eligibleHeading) ||
                (heading.tagName !== "H1" &&
                    !stableFragment.test(heading.id)) ||
                heading.closest("dialog, .ui-card, .article-card")
            )
                continue;
            const existing = heading.querySelector(":scope > a.heading-copy");
            const label =
                [...heading.childNodes]
                    .filter((node) => node !== existing)
                    .map((node) => node.textContent || "")
                    .join("")
                    .trim() || heading.id;
            const control = existing || document.createElement("a");
            const page = heading.tagName === "H1";
            control.id = `heading-copy-${page ? "page" : heading.id}`;
            control.className = "heading-copy";
            control.href = page
                ? localRoute(window.location.pathname, window.location.search)
                : `#${heading.id}`;
            if (control.dataset.heading !== label) {
                control.textContent = "#";
                control.dataset.heading = label;
            }
            const name = messages.headingCopyLabel.replace("{heading}", label);
            control.title = name;
            control.setAttribute("aria-label", name);
            heading.classList.add("has-heading-link");
            heading.setAttribute("aria-label", label);
            if (!existing) heading.prepend(control);
        }
    };
    enhance(document);
    const changes = new MutationObserver((records) => {
        for (const record of records) {
            if (record.target instanceof Element) {
                const heading = record.target.closest(eligibleHeading);
                if (heading) enhance(heading);
            }
            for (const node of record.addedNodes)
                if (node instanceof Element) enhance(node);
        }
    });
    changes.observe(document.body, { childList: true, subtree: true });
    const refreshPageTarget = (event) => {
        if (!(event.target instanceof Element)) return;
        const control = event.target.closest(
            ".page-heading h1 > a.heading-copy",
        );
        if (control)
            control.href = localRoute(
                window.location.pathname,
                window.location.search,
            );
    };
    document.addEventListener("pointerover", refreshPageTarget);
    document.addEventListener("contextmenu", refreshPageTarget);
    document.addEventListener("click", (event) => {
        refreshPageTarget(event);
        if (!(event.target instanceof Element)) return;
        const control = event.target.closest("a.heading-copy");
        const heading = control?.closest("h1, h2[id]");
        if (
            !control ||
            !heading ||
            event.button !== 0 ||
            event.ctrlKey ||
            event.metaKey ||
            event.shiftKey ||
            event.altKey
        )
            return;
        event.preventDefault();
        if (control.getAttribute("aria-busy") === "true") return;
        const label = heading.getAttribute("aria-label") || heading.id;
        const page = heading.tagName === "H1";
        const source = page
            ? pageWikilink(
                  window.location.pathname,
                  label,
                  window.location.search,
              )
            : headingWikilink(
                  window.location.pathname,
                  heading.id,
                  label,
                  window.location.search,
              );
        control.setAttribute("aria-busy", "true");
        announcement.textContent = "";
        void (async () => {
            try {
                if (!navigator.clipboard?.writeText)
                    throw new Error("Clipboard unavailable");
                await navigator.clipboard.writeText(source);
                announcement.textContent = messages.headingCopySuccess;
                control.title = messages.headingCopySuccess;
                control.textContent = "\u2713";
            } catch {
                selectManually(source, control, page);
            } finally {
                control.removeAttribute("aria-busy");
            }
        })();
    });
}
export { headingWikilink, initializeHeadingLinks, pageWikilink };
