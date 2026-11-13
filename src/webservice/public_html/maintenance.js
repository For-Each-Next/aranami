function maintenanceURL(value) {
    const url = new URL(value, location.href);
    return url.origin === location.origin &&
        url.pathname === "/maintenance/" &&
        url.searchParams.has("task")
        ? url
        : void 0;
}
function focusTarget(element) {
    if (
        element instanceof HTMLInputElement &&
        element.id === "maintenance-query"
    ) {
        return {
            kind: "search",
            start: element.selectionStart,
            end: element.selectionEnd,
        };
    }
    if (element?.id === "maintenance-limit") return { kind: "size" };
    if (element?.closest(".maintenance-menu")) return { kind: "menu" };
    const pager = element?.closest("[data-page]");
    if (pager?.dataset.page) return { kind: "page", page: pager.dataset.page };
    const sort = element?.closest("form")?.querySelector('input[name="sort"]');
    if (sort && element?.closest(".maintenance-table"))
        return { kind: "sort", column: sort.value };
    if (element?.closest(".maintenance-search, .maintenance-table-actions")) {
        return { kind: "search", start: null, end: null };
    }
    if (element?.closest(".maintenance-worklist")) return { kind: "table" };
}
function restoreFocus(root, target) {
    if (!target) return;
    let control = null;
    if (target.kind === "sort") {
        for (const form of root.querySelectorAll(".maintenance-table form")) {
            if (
                form.querySelector('input[name="sort"]')?.value ===
                target.column
            ) {
                control = form.querySelector('button[type="submit"]');
                break;
            }
        }
    } else if (target.kind === "search") {
        control = root.querySelector("#maintenance-query");
    } else if (target.kind === "size") {
        control = root.querySelector("#maintenance-limit");
    } else if (target.kind === "menu") {
        control = root.querySelector(".maintenance-menu summary");
    } else if (target.kind === "page") {
        control =
            [...root.querySelectorAll("[data-page]")].find(
                (element) =>
                    element.dataset.page === target.page &&
                    !element.matches(":disabled"),
            ) ?? null;
    }
    (control ?? root.querySelector(".table-scroll"))?.focus({
        preventScroll: true,
    });
    if (
        target.kind === "search" &&
        control instanceof HTMLInputElement &&
        target.start !== null &&
        target.end !== null
    ) {
        control.setSelectionRange(target.start, target.end);
    }
}
function initializeMaintenance(components) {
    if (
        typeof fetch !== "function" ||
        typeof DOMParser !== "function" ||
        typeof AbortController !== "function"
    )
        return;
    const root = document.querySelector("#maintenance-browser");
    let worklist = root?.querySelector(".maintenance-worklist");
    const menu = root?.querySelector(".maintenance-menu > ul");
    let source = root?.querySelector(".maintenance-source");
    if (
        !root ||
        !worklist ||
        !menu ||
        !source ||
        root.hasAttribute("data-maintenance-async")
    )
        return;
    root.setAttribute("data-maintenance-async", "");
    let displayedURL = new URL(location.href);
    let requestNumber = 0;
    let controller;
    async function navigate(url, push, trigger) {
        const request = ++requestNumber;
        controller?.abort();
        controller = new AbortController();
        const signal = controller.signal;
        const initialFocus = document.activeElement;
        const requestedFocus = focusTarget(trigger ?? initialFocus);
        worklist.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(url, {
                signal,
                credentials: "same-origin",
                redirect: "error",
                headers: { Accept: "text/html" },
            });
            if (
                !response.ok ||
                (response.url && !maintenanceURL(response.url)) ||
                response.headers
                    .get("Content-Type")
                    ?.split(";")[0]
                    .trim()
                    .toLowerCase() !== "text/html"
            ) {
                throw new Error("Maintenance page unavailable");
            }
            const page = new DOMParser().parseFromString(
                await response.text(),
                "text/html",
            );
            if (request !== requestNumber) return;
            const nextRoot = page.querySelector("#maintenance-browser");
            const replacement = nextRoot?.querySelector(
                ".maintenance-worklist",
            );
            const nextMenu = nextRoot?.querySelector(".maintenance-menu > ul");
            const nextSource = nextRoot?.querySelector(".maintenance-source");
            const task = replacement?.querySelector(
                'input[name="task"]',
            )?.value;
            if (
                !replacement ||
                !nextMenu ||
                !nextSource ||
                !replacement.querySelector(".maintenance-table") ||
                task !== url.searchParams.get("task") ||
                nextRoot?.querySelector("script, iframe, object, embed")
            ) {
                throw new Error("Invalid maintenance response");
            }
            const active = document.activeElement;
            const focus =
                active === initialFocus
                    ? requestedFocus
                    : worklist.contains(active)
                      ? focusTarget(active)
                      : void 0;
            const scrollLeft =
                worklist.querySelector(".table-scroll")?.scrollLeft ?? 0;
            worklist.replaceWith(replacement);
            worklist = replacement;
            menu.replaceChildren(...nextMenu.childNodes);
            source.replaceWith(nextSource);
            source = nextSource;
            components.enhance(worklist);
            const scroll = worklist.querySelector(".table-scroll");
            if (scroll) scroll.scrollLeft = scrollLeft;
            for (const link of document.querySelectorAll(
                ".language-switcher a[lang]",
            )) {
                const next = [
                    ...page.querySelectorAll(".language-switcher a[lang]"),
                ].find((candidate) => candidate.lang === link.lang);
                if (next) link.setAttribute("href", next.getAttribute("href"));
            }
            const heading = page.querySelector(".page-heading h1");
            const currentHeading = document.querySelector(".page-heading h1");
            if (heading && currentHeading)
                currentHeading.textContent = heading.textContent;
            document.title = page.title;
            for (const selector of [
                ".contents-desktop > ul",
                ".contents-mobile .page-toc > ul",
            ]) {
                const next = page.querySelector(selector);
                if (next)
                    document
                        .querySelector(selector)
                        ?.replaceChildren(...next.childNodes);
            }
            if (push) history.pushState(null, "", url);
            displayedURL = url;
            restoreFocus(root, focus);
        } catch {
            if (request === requestNumber && !signal.aborted)
                location.assign(url.href);
        } finally {
            if (request === requestNumber)
                worklist.removeAttribute("aria-busy");
        }
    }
    root.addEventListener("submit", (event) => {
        if (
            event.defaultPrevented ||
            !(event.target instanceof HTMLFormElement)
        )
            return;
        const form = event.target;
        if (
            form.method.toLowerCase() !== "get" ||
            (form.target && form.target !== "_self")
        )
            return;
        const url = new URL(form.action, location.href);
        const data = new FormData(form, event.submitter);
        const parameters = new URLSearchParams();
        for (const [name, value] of data) {
            if (typeof value !== "string") return;
            parameters.append(name, value);
        }
        url.search = parameters.toString();
        if (!maintenanceURL(url.href)) return;
        event.preventDefault();
        void navigate(
            url,
            true,
            document.activeElement === document.body
                ? event.submitter
                : document.activeElement,
        );
    });
    root.addEventListener("click", (event) => {
        if (
            event.defaultPrevented ||
            event.button !== 0 ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey
        )
            return;
        if (!(event.target instanceof Element)) return;
        const link = event.target.closest(
            ".ui-table-pager a[href], .maintenance-menu a[href], .maintenance-table-actions > a[href]",
        );
        if (
            !link ||
            link.hasAttribute("download") ||
            (link.target && link.target !== "_self")
        )
            return;
        const url = maintenanceURL(link.href);
        if (!url) return;
        event.preventDefault();
        void navigate(url, true, link);
    });
    window.addEventListener("popstate", () => {
        if (
            location.pathname === displayedURL.pathname &&
            location.search === displayedURL.search
        ) {
            ++requestNumber;
            controller?.abort();
            worklist.removeAttribute("aria-busy");
            return;
        }
        const url = maintenanceURL(location.href);
        if (url) void navigate(url, false);
        else location.assign(location.href);
    });
}
export { initializeMaintenance };
