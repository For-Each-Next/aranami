/** Sort and paginate saved editor tables without replacing their native cells. */

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
export function initializeQualityAuthorship() {
    for (const form of document.querySelectorAll(
        "[data-quality-authorship-filter]:not([data-auto-submit])",
    )) initializeQualityFilter(form);
    for (const [index, host] of document
        .querySelectorAll("[data-quality-authorship-table]")
        .entries()) {
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
        label.htmlFor = `quality-page-size-${index}`;
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
        status.setAttribute("role", "status");
        pager.append(label, status);
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
            pager.append(button);
        }
        select.addEventListener("change", () => {
            size = Number(select.value);
            page = 1;
            render();
        });
        if (originalRows.length > 10) host.append(pager);

        /** Reorder existing escaped cells and show only the selected page. */
        function render() {
            const rows = [...originalRows].sort((left, right) => {
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
        const dialog = host.closest("dialog");
        if (dialog) {
            for (const opener of document.querySelectorAll(
                `a[aria-controls="${dialog.id}"]`,
            )) {
                opener.addEventListener("click", () => {
                    column = defaultColumn;
                    descending = true;
                    page = 1;
                    render();
                });
            }
        }
        render();
    }
}
