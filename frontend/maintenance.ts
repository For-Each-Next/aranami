/** Navigate saved maintenance lists without replacing the surrounding page. */

type Components = { enhance(root: ParentNode): void; dispose(root: ParentNode): void };
type FocusTarget =
    | { kind: "sort"; column: string }
    | { kind: "search"; start: number | null; end: number | null }
    | { kind: "size" | "menu" | "table" }
    | { kind: "page"; page: string };

function maintenanceURL(value: string): URL | undefined {
    const url = new URL(value, location.href);
    return url.origin === location.origin && url.pathname === "/maintenance/" && url.searchParams.has("task")
        ? url : undefined;
}

function focusTarget(element: Element | null): FocusTarget | undefined {
    if (element instanceof HTMLInputElement && element.id === "maintenance-query") {
        return { kind: "search", start: element.selectionStart, end: element.selectionEnd };
    }
    if (element?.id === "maintenance-limit") return { kind: "size" };
    if (element?.closest(".maintenance-menu")) return { kind: "menu" };
    const pager = element?.closest<HTMLElement>("[data-page]");
    if (pager?.dataset.page) return { kind: "page", page: pager.dataset.page };
    const sort = element?.closest("form")?.querySelector<HTMLInputElement>('input[name="sort"]');
    if (sort && element?.closest(".maintenance-table")) return { kind: "sort", column: sort.value };
    if (element?.closest(".maintenance-search, .maintenance-table-actions")) {
        return { kind: "search", start: null, end: null };
    }
    if (element?.closest(".maintenance-worklist")) return { kind: "table" };
}

function restoreFocus(root: HTMLElement, target: FocusTarget | undefined): void {
    if (!target) return;
    let control: HTMLElement | null = null;
    if (target.kind === "sort") {
        for (const form of root.querySelectorAll<HTMLFormElement>(".maintenance-table form")) {
            if (form.querySelector<HTMLInputElement>('input[name="sort"]')?.value === target.column) {
                control = form.querySelector<HTMLButtonElement>('button[type="submit"]');
                break;
            }
        }
    } else if (target.kind === "search") {
        control = root.querySelector<HTMLInputElement>("#maintenance-query");
    } else if (target.kind === "size") {
        control = root.querySelector<HTMLSelectElement>("#maintenance-limit");
    } else if (target.kind === "menu") {
        control = root.querySelector<HTMLElement>(".maintenance-menu summary");
    } else if (target.kind === "page") {
        control = [...root.querySelectorAll<HTMLElement>("[data-page]")]
            .find(element => element.dataset.page === target.page && !element.matches(":disabled")) ?? null;
    }
    (control ?? root.querySelector<HTMLElement>(".table-scroll"))?.focus({ preventScroll: true });
    if (target.kind === "search" && control instanceof HTMLInputElement && target.start !== null && target.end !== null) {
        control.setSelectionRange(target.start, target.end);
    }
}

/** Keep native GET controls as the fallback for unavailable or invalid responses. */
export function initializeMaintenance(components: Components): void {
    if (typeof fetch !== "function" || typeof DOMParser !== "function" || typeof AbortController !== "function") return;
    const root = document.querySelector<HTMLElement>("#maintenance-browser");
    let worklist = root?.querySelector<HTMLElement>(".maintenance-worklist");
    const menu = root?.querySelector<HTMLUListElement>(".maintenance-menu > ul");
    let source = root?.querySelector<HTMLElement>(".maintenance-source");
    if (!root || !worklist || !menu || !source || root.hasAttribute("data-maintenance-async")) return;
    root.setAttribute("data-maintenance-async", "");
    let displayedURL = new URL(location.href);
    let requestNumber = 0;
    let controller: AbortController | undefined;

    async function navigate(url: URL, push: boolean, trigger?: Element | null): Promise<void> {
        const request = ++requestNumber;
        controller?.abort();
        controller = new AbortController();
        const signal = controller.signal;
        const initialFocus = document.activeElement;
        const requestedFocus = focusTarget(trigger ?? initialFocus);
        worklist!.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(url, {
                signal,
                credentials: "same-origin",
                redirect: "error",
                headers: { Accept: "text/html" },
            });
            if (!response.ok || response.url && !maintenanceURL(response.url)
                || response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "text/html") {
                throw new Error("Maintenance page unavailable");
            }
            const page = new DOMParser().parseFromString(await response.text(), "text/html");
            if (request !== requestNumber) return;
            const nextRoot = page.querySelector<HTMLElement>("#maintenance-browser");
            const replacement = nextRoot?.querySelector<HTMLElement>(".maintenance-worklist");
            const nextMenu = nextRoot?.querySelector<HTMLUListElement>(".maintenance-menu > ul");
            const nextSource = nextRoot?.querySelector<HTMLElement>(".maintenance-source");
            const task = replacement?.querySelector<HTMLInputElement>('input[name="task"]')?.value;
            if (!replacement || !nextMenu || !nextSource || !replacement.querySelector(".maintenance-table")
                || task !== url.searchParams.get("task") || nextRoot?.querySelector("script, iframe, object, embed")) {
                throw new Error("Invalid maintenance response");
            }
            const active = document.activeElement;
            const focus = active === initialFocus ? requestedFocus
                : worklist!.contains(active) ? focusTarget(active) : undefined;
            const scrollLeft = worklist!.querySelector<HTMLElement>(".table-scroll")?.scrollLeft ?? 0;
            components.dispose(worklist!);
            worklist!.replaceWith(replacement);
            worklist = replacement;
            menu!.replaceChildren(...nextMenu.childNodes);
            source!.replaceWith(nextSource);
            source = nextSource;
            components.enhance(worklist);
            const scroll = worklist.querySelector<HTMLElement>(".table-scroll");
            if (scroll) scroll.scrollLeft = scrollLeft;
            for (const link of document.querySelectorAll<HTMLAnchorElement>(".language-switcher a[lang]")) {
                const next = [...page.querySelectorAll<HTMLAnchorElement>(".language-switcher a[lang]")]
                    .find(candidate => candidate.lang === link.lang);
                if (next) link.setAttribute("href", next.getAttribute("href")!);
            }
            const heading = page.querySelector(".page-heading h1");
            const currentHeading = document.querySelector(".page-heading h1");
            if (heading && currentHeading) currentHeading.textContent = heading.textContent;
            document.title = page.title;
            for (const selector of [".contents-desktop > ul", ".contents-mobile .page-toc > ul"]) {
                const next = page.querySelector(selector);
                if (next) document.querySelector(selector)?.replaceChildren(...next.childNodes);
            }
            if (push) history.pushState(null, "", url);
            displayedURL = url;
            restoreFocus(root!, focus);
        } catch {
            if (request === requestNumber && !signal.aborted) location.assign(url.href);
        } finally {
            if (request === requestNumber) worklist!.removeAttribute("aria-busy");
        }
    }

    root.addEventListener("submit", event => {
        if (event.defaultPrevented || !(event.target instanceof HTMLFormElement)) return;
        const form = event.target;
        if (form.method.toLowerCase() !== "get" || form.target && form.target !== "_self") return;
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
        void navigate(url, true, document.activeElement === document.body ? event.submitter : document.activeElement);
    });
    root.addEventListener("click", event => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (!(event.target instanceof Element)) return;
        const link = event.target.closest<HTMLAnchorElement>(
            ".cdx-table-pager a[href], .maintenance-menu a[href], .maintenance-table-actions > a[href]",
        );
        if (!link || link.hasAttribute("download") || link.target && link.target !== "_self") return;
        const url = maintenanceURL(link.href);
        if (!url) return;
        event.preventDefault();
        void navigate(url, true, link);
    });
    window.addEventListener("popstate", () => {
        if (location.pathname === displayedURL.pathname && location.search === displayedURL.search) {
            ++requestNumber;
            controller?.abort();
            worklist!.removeAttribute("aria-busy");
            return;
        }
        const url = maintenanceURL(location.href);
        if (url) void navigate(url, false);
        else location.assign(location.href);
    });
}
