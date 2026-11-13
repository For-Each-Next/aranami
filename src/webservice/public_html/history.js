function historyURL(value) {
    const url = new URL(value, location.href);
    return url.origin === location.origin && url.pathname === "/reports/"
        ? url
        : void 0;
}
function initializeHistory() {
    const root = document.querySelector("#report-history");
    const host = root?.querySelector("[data-execution-history]");
    if (!root || !host || root.hasAttribute("data-history-enhanced")) return;
    root.setAttribute("data-history-enhanced", "");
    let requestNumber = 0;
    let controller;
    let displayedURL = new URL(location.href);
    async function navigate(url, push) {
        if (
            !historyURL(url.href) ||
            typeof fetch !== "function" ||
            typeof DOMParser !== "function" ||
            typeof AbortController !== "function"
        ) {
            location.assign(url.href);
            return;
        }
        const request = ++requestNumber;
        controller?.abort();
        controller = new AbortController();
        const signal = controller.signal;
        const initialFocus = document.activeElement;
        const label = initialFocus?.getAttribute("aria-label");
        root.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(url, {
                signal,
                credentials: "same-origin",
                redirect: "error",
                headers: { Accept: "text/html" },
            });
            if (
                !response.ok ||
                (response.url && !historyURL(response.url)) ||
                response.headers
                    .get("Content-Type")
                    ?.split(";")[0]
                    .trim()
                    .toLowerCase() !== "text/html"
            ) {
                throw new Error("Report history unavailable");
            }
            const page = new DOMParser().parseFromString(
                await response.text(),
                "text/html",
            );
            if (request !== requestNumber) return;
            const replacement = page.querySelector("#report-history");
            const next = replacement?.querySelector(
                "[data-execution-history]",
            );
            if (
                !replacement ||
                !next?.querySelector("table") ||
                !Number.isSafeInteger(Number(next.dataset.page)) ||
                Number(next.dataset.page) < 1 ||
                !Number.isSafeInteger(Number(next.dataset.total)) ||
                Number(next.dataset.total) < 1 ||
                replacement.querySelector("script, iframe, object, embed")
            ) {
                throw new Error("Invalid report history response");
            }
            const focus =
                document.activeElement === initialFocus &&
                (root.contains(initialFocus) ||
                    (!push && initialFocus === document.body));
            const scrollLeft =
                root.querySelector(".ui-table__table-wrapper")?.scrollLeft ??
                0;
            for (const dialog of root.querySelectorAll("dialog[open]")) {
                const updated = replacement.querySelector(`#${dialog.id}`);
                if (updated) updated.replaceWith(dialog);
                else replacement.append(dialog);
            }
            root.replaceChildren(...replacement.childNodes);
            window.aranamiComponents?.enhance(root);
            const scroll = root.querySelector(".ui-table__table-wrapper");
            if (scroll) scroll.scrollLeft = scrollLeft;
            if (push) history.pushState(null, "", url);
            displayedURL = url;
            for (const link of document.querySelectorAll(
                ".language-switcher a[lang]",
            )) {
                const translated = [
                    ...page.querySelectorAll(".language-switcher a[lang]"),
                ].find((candidate) => candidate.lang === link.lang);
                if (translated)
                    link.setAttribute("href", translated.getAttribute("href"));
            }
            if (focus && !root.querySelector("dialog[open]")) {
                const button = label
                    ? [...root.querySelectorAll("a[href], button")].find(
                          (candidate) =>
                              candidate.getAttribute("aria-label") === label &&
                              !candidate.disabled,
                      )
                    : void 0;
                (button ?? root).focus({ preventScroll: true });
            }
        } catch {
            if (request === requestNumber && !signal.aborted)
                location.assign(url.href);
        } finally {
            if (request === requestNumber) root.removeAttribute("aria-busy");
        }
    }
    root.addEventListener("click", (event) => {
        if (
            event.defaultPrevented ||
            event.button !== 0 ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey ||
            !(event.target instanceof Element)
        )
            return;
        const link = event.target.closest(".history-pagination a[href]");
        if (
            !link ||
            link.hasAttribute("download") ||
            (link.target && link.target !== "_self")
        )
            return;
        const url = historyURL(link.href);
        if (!url) return;
        event.preventDefault();
        void navigate(url, true);
    });
    window.addEventListener("popstate", () => {
        if (
            location.pathname === displayedURL.pathname &&
            location.search === displayedURL.search
        ) {
            ++requestNumber;
            controller?.abort();
            root.removeAttribute("aria-busy");
            return;
        }
        const url = historyURL(location.href);
        if (url) void navigate(url, false);
        else location.assign(location.href);
    });
}
export { initializeHistory };
