/** Sort and paginate saved editor tables without replacing their native cells. */

let detailHandlersInstalled = false;
let tableSequence = 0;
const detailRequests = new Map();

/** Keep delayed details consistent with the currently selected matrix cohort. */
export function applyQualityDetailCohort(dialog) {
    const form = document.querySelector("[data-quality-matrix]");
    if (form && !form.qualitySelectedIds && form.dataset.selectedPageIds)
        form.qualitySelectedIds = new Set(JSON.parse(form.dataset.selectedPageIds).map(String));
    const ids = form?.qualitySelectedIds;
    if (!ids) return;
    for (const row of dialog.querySelectorAll("[data-matrix-page]"))
        row.dataset.cohort = ids.has(row.dataset.matrixPage) ? "1" : "0";
}

/** Fetch only the requested same-origin dialog and share concurrent attempts. */
export function loadQualityDetail(value, id) {
    const url = new URL(value, location.href);
    if (url.origin !== location.origin) return Promise.reject(new Error("Invalid quality detail URL"));
    url.hash = "";
    const key = `${url.href}#${id}`;
    const existing = document.getElementById(id);
    if (existing?.nodeName === "DIALOG" && existing.dataset.qualityDetailUrl === url.href)
        return Promise.resolve(existing);
    if (detailRequests.has(key)) return detailRequests.get(key);
    const request = (async () => {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch(url, {
                credentials: "same-origin", redirect: "error", signal: controller.signal,
                headers: {Accept: "text/html"},
            });
            if (!response.ok || (response.url && new URL(response.url).origin !== location.origin) ||
                    !response.headers.get("content-type")?.startsWith("text/html"))
                throw new Error("Quality detail unavailable");
            const page = new DOMParser().parseFromString(await response.text(), "text/html");
            const dialog = page.getElementById(id);
            if (dialog?.nodeName !== "DIALOG" ||
                    dialog.querySelector("script, iframe, object, embed, base, link, style"))
                throw new Error("Invalid quality detail");
            dialog.removeAttribute("open");
            dialog.dataset.qualityDetailUrl = url.href;
            return dialog;
        } finally {clearTimeout(timeout);}
    })();
    detailRequests.set(key, request);
    request.then(() => detailRequests.delete(key), () => detailRequests.delete(key));
    return request;
}

/** Enhance native detail links while retaining modified-click navigation. */
export function initializeQualityDetails() {
    if (detailHandlersInstalled) return;
    detailHandlersInstalled = true;
    let intent = 0;
    let pendingIndicator = null;
    const openers = new WeakMap();
    function clearPendingIndicator() {
        if (!pendingIndicator) return;
        pendingIndicator.link.removeAttribute("aria-busy");
        pendingIndicator.status.textContent = "";
        pendingIndicator = null;
    }
    document.addEventListener("keydown", event => {
        if (event.key === "Escape") {intent++; clearPendingIndicator();}
    });
    document.addEventListener("close", event => {
        const opener = openers.get(event.target);
        if (opener?.isConnected) opener.focus();
    }, true);
    document.addEventListener("click", async event => {
        const link = event.target.closest?.("a[data-quality-detail]");
        if (!link || event.defaultPrevented || event.button || event.ctrlKey || event.metaKey ||
                event.shiftKey || event.altKey || link.hasAttribute("download") ||
                (link.target && link.target !== "_self") || typeof fetch !== "function" ||
                typeof DOMParser !== "function" || typeof HTMLDialogElement === "undefined" ||
                typeof HTMLDialogElement.prototype.showModal !== "function") return;
        const url = new URL(link.href, location.href);
        if (url.origin !== location.origin) return;
        event.preventDefault();
        event.stopPropagation();
        clearPendingIndicator();
        const request = ++intent;
        const id = link.dataset.qualityDetail;
        link.setAttribute("aria-busy", "true");
        let status = link.qualityDetailStatus;
        if (!status) {
            status = document.createElement("span");
            status.className = "context";
            status.setAttribute("role", "status");
            link.after(status);
            link.qualityDetailStatus = status;
        }
        const indicator = document.createElement("span");
        indicator.className = "quality-detail-loading";
        indicator.setAttribute("aria-hidden", "true");
        const announcement = document.createElement("span");
        announcement.className = "visually-hidden";
        announcement.textContent = link.dataset.loading || "Loading details…";
        status.replaceChildren(indicator, announcement);
        pendingIndicator = {link, status, request};
        let opened = false;
        try {
            const incoming = await loadQualityDetail(url.href, id);
            if (request !== intent || !link.isConnected) return;
            let dialog = document.getElementById(id);
            if (dialog !== incoming) {
                dialog?.close();
                dialog?.remove();
                (document.querySelector("[data-quality-results]") || document.body).append(incoming);
                dialog = incoming;
            }
            applyQualityDetailCohort(dialog);
            initializeQualityAuthorship(dialog);
            dialog.dispatchEvent(new Event("quality-detail-open"));
            openers.set(dialog, link);
            link.focus();
            if (!dialog.open) dialog.showModal();
            opened = true;
        } catch {
            if (request === intent && link.isConnected)
                status.textContent = ` ${link.dataset.loadFailed || "Details could not load. Try again or open the link in a new tab."}`;
        } finally {
            if (request === intent) {
                link.removeAttribute("aria-busy");
                if (opened || !link.isConnected) status.textContent = "";
                if (pendingIndicator?.request === request) pendingIndicator = null;
            }
        }
    }, true);
    // Legacy pages retain inline dialogs; enhance each only when it is opened.
    document.addEventListener("click", event => {
        const link = event.target.closest?.('a[aria-haspopup="dialog"]');
        if (!link || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey ||
                link.hasAttribute("data-quality-detail")) return;
        const dialog = document.getElementById(link.getAttribute("aria-controls"));
        if (dialog?.nodeName === "DIALOG") {
            applyQualityDetailCohort(dialog);
            initializeQualityAuthorship(dialog);
            dialog.dispatchEvent(new Event("quality-detail-open"));
        }
    }, true);
}

/** Compare observed values while keeping missing observations last. */
export function compareValues(left, right, descending, locale) {
    if (left === null) return right === null ? 0 : 1;
    if (right === null) return -1;
    const comparison =
        typeof left === "number" && typeof right === "number"
            ? left - right
            : String(left).localeCompare(String(right), locale);
    return descending ? -comparison : comparison;
}

/** Update only the saved author results, keeping the toggle focused. */
function initializeQualityFilter(form) {
    const input = form.querySelector('[name="count_b"]');
    const results = document.querySelector("[data-quality-authorship-results]");
    if (!input || !results) return;
    form.dataset.autoSubmit = "";
    if (typeof fetch !== "function" || typeof DOMParser !== "function") {
        input.addEventListener("change", () => form.requestSubmit());
        return;
    }
    const status = form.querySelector("[data-quality-authorship-status]");
    let applied = input.checked;
    let requestNumber = 0;
    let controller;

    /** Reject stale responses and retain the last successful list on failure. */
    async function update(push) {
        const request = ++requestNumber;
        controller?.abort();
        controller = new AbortController();
        const selected = input.checked;
        const url = new URL(form.action, location.href);
        url.search = new URLSearchParams(new FormData(form)).toString();
        results.setAttribute("aria-busy", "true");
        status.textContent = form.dataset.updating;
        try {
            const response = await fetch(url, {
                credentials: "same-origin",
                headers: { Accept: "text/html" },
                redirect: "error",
                signal: controller.signal,
            });
            if (!response.ok || !response.headers.get("content-type")?.includes("text/html"))
                throw new Error("Author results unavailable");
            const page = new DOMParser().parseFromString(await response.text(), "text/html");
            if (request !== requestNumber) return;
            const incoming = page.querySelector("[data-quality-authorship-results]");
            if (!incoming || incoming.querySelector("script, iframe, object, embed") ||
                incoming.dataset.countB !== (selected ? "1" : "0"))
                throw new Error("Invalid author results");
            results.replaceChildren(...incoming.childNodes);
            results.dataset.countB = incoming.dataset.countB;
            applied = selected;
            initializeQualityAuthorship();
            if (push && url.href !== location.href) window.history.pushState(null, "", url);
            for (const link of document.querySelectorAll(".language-switcher a[hreflang]")) {
                const destination = new URL(link.href, location.href);
                if (selected) destination.searchParams.set("count_b", "1");
                else destination.searchParams.delete("count_b");
                link.href = destination.href;
            }
            status.textContent = "";
        } catch (error) {
            if (request !== requestNumber || error.name === "AbortError") return;
            input.checked = applied;
            status.textContent = form.dataset.updateFailed;
        } finally {
            if (request === requestNumber) results.removeAttribute("aria-busy");
        }
    }
    input.addEventListener("change", () => update(true));
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        update(true);
    });
    window.addEventListener("popstate", () => {
        input.checked = new URL(location.href).searchParams.get("count_b") === "1";
        update(false);
    });
}

/** Add accessible sorting and pagination to each server-rendered directory. */
export function initializeQualityAuthorship(root = document) {
    initializeQualityDetails();
    for (const form of document.querySelectorAll(
        "[data-quality-authorship-filter]:not([data-auto-submit])",
    )) initializeQualityFilter(form);
    for (const host of root.querySelectorAll("[data-quality-authorship-table]")) {
        const dialog = host.closest("dialog");
        if (root === document && dialog && !dialog.open) continue;
        const table = host.querySelector("table");
        if (!table || host.hasAttribute("data-table-enhanced")) continue;
        host.setAttribute("data-table-enhanced", "");
        const tbody = table.querySelector("tbody");
        const headings = [...table.querySelectorAll("thead th")];
        const originalRows = [...tbody.rows];
        const locale = document.documentElement.lang;
        const defaultColumn = Number(
            (host.dataset.defaultSort || "column3").replace("column", ""),
        );
        let column = defaultColumn;
        let descending = true;
        let page = 1;
        let size = 10;
        const buttons = [];
        const pager = document.createElement("nav");
        pager.className = "ui-table-pager";
        pager.setAttribute(
            "aria-label",
            table.querySelector("caption")?.textContent || "",
        );
        const label = document.createElement("label");
        label.className = "ui-table-pager__start";
        label.htmlFor = `quality-page-size-${++tableSequence}`;
        label.textContent = host.dataset.pageSize;
        const select = document.createElement("select");
        select.id = label.htmlFor;
        for (const count of [10, 20, 50]) {
            const option = document.createElement("option");
            option.value = String(count);
            option.textContent =
                host.dataset.rows?.replace("{count}", String(count)) ||
                String(count);
            select.append(option);
        }
        label.append(select);
        const status = document.createElement("span");
        status.className = "ui-table-pager__center";
        status.setAttribute("role", "status");
        pager.append(label, status);
        const navigation = document.createElement("div");
        navigation.className = "ui-table-pager__end";
        pager.append(navigation);
        const actions = [];
        for (const [key, destination] of [
            ["first", () => 1],
            ["prev", () => page - 1],
            ["next", () => page + 1],
            ["last", () => Math.max(1, Math.ceil(originalRows.length / size))],
        ]) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "ui-button";
            button.textContent = host.dataset[key];
            button.setAttribute("aria-label", host.dataset[key]);
            button.addEventListener("click", () => {
                page = destination();
                render();
            });
            actions.push(button);
            navigation.append(button);
        }
        select.addEventListener("change", () => {
            size = Number(select.value);
            page = 1;
            render();
        });
        if (originalRows.length > 10) host.append(pager);

        /** Reorder existing escaped cells and show only the selected page. */
        function render() {
            originalRows.forEach(row => {row.hidden = true;});
            const rows = originalRows.filter(row => row.dataset.cohort !== "0").sort((left, right) => {
                const read = (row) => {
                    const value = row.cells[column].dataset.sort;
                    return value === undefined || value === ""
                        ? null
                        : headings[column].dataset.sortType === "number"
                          ? Number(value)
                          : value;
                };
                return compareValues(
                    read(left),
                    read(right),
                    descending,
                    locale,
                );
            });
            page = Math.min(page, Math.max(1, Math.ceil(rows.length / size)));
            const start = (page - 1) * size;
            for (const [position, row] of rows.entries()) {
                row.hidden =
                    originalRows.length > 10 &&
                    (position < start || position >= start + size);
                row.cells[0].textContent = String(position + 1);
                tbody.append(row);
            }
            for (const [position, heading] of headings.entries()) {
                if (position > 0)
                    heading.setAttribute(
                        "aria-sort",
                        position === column
                            ? descending
                                ? "descending"
                                : "ascending"
                            : "none",
                    );
            }
            status.textContent =
                host.dataset.pageStatus
                    ?.replace("{start}", String(rows.length ? start + 1 : 0))
                    .replace(
                        "{end}",
                        String(Math.min(start + size, rows.length)),
                    )
                    .replace("{total}", String(rows.length)) || "";
            actions[0].disabled = actions[1].disabled = page <= 1;
            actions[2].disabled = actions[3].disabled =
                start + size >= rows.length;
        }
        for (const [position, heading] of headings.entries()) {
            if (!position) continue;
            const button = document.createElement("button");
            button.type = "button";
            button.className = "ui-table__table__sort-button";
            button.textContent = heading.textContent;
            button.title =
                host.dataset.sortCaption?.replace(
                    "{caption}",
                    heading.textContent,
                ) || heading.textContent;
            button.addEventListener("click", () => {
                descending = column === position ? !descending : false;
                column = position;
                page = 1;
                render();
            });
            buttons.push(button);
            heading.replaceChildren(button);
        }
        const cohortChanged = () => {
            if (!host.isConnected) {
                document.removeEventListener("quality-cohort-change", cohortChanged);
                return;
            }
            page = 1;
            render();
        };
        document.addEventListener("quality-cohort-change", cohortChanged);
        if (dialog) {
            dialog.addEventListener("quality-detail-open", () => {
                column = defaultColumn;
                descending = true;
                page = 1;
                render();
            });
        }
        render();
    }
}
