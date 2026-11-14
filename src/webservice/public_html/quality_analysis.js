/** Filter and sort the complete saved article list without source requests. */
import { compareValues, initializeQualityAuthorship } from "./quality_authorship.js";

/** Compare existing cell observations while leaving unknown values last. */
export function compareAnalysisRows(left, right, column, descending, locale) {
    const read = (row) => {
        const value = row.cells[column].dataset.sort;
        return value === undefined || value === "" ? null :
            [2, 3, 6, 7].includes(column) ? Number(value) : value;
    };
    return compareValues(read(left), read(right), descending, locale);
}

/** Enhance the native table while preserving its escaped cells and links. */
export function initializeQualityAnalysis(previous = {}) {
    const host = document.querySelector("[data-quality-analysis]");
    if (!host || host.hasAttribute("data-analysis-enhanced")) return;
    host.setAttribute("data-analysis-enhanced", "");
    const rows = [...host.querySelectorAll("[data-analysis-row]")];
    const table = host.querySelector("table");
    const body = table.tBodies[0];
    const headings = [...table.querySelectorAll("thead th")];
    const search = host.querySelector("[data-analysis-search]");
    const grade = host.querySelector("[data-analysis-grade-filter]");
    const importance = host.querySelector("[data-analysis-importance-filter]");
    const size = host.querySelector("[data-analysis-size]");
    const pager = host.querySelector("[data-analysis-pager]");
    const result = host.querySelector("[data-analysis-result-count]");
    const pageStatus = host.querySelector("[data-analysis-page-status]");
    const empty = host.querySelector("[data-analysis-no-matches]");
    const locale = document.documentElement.lang;
    search.value = previous.search || "";
    grade.value = previous.grade || "";
    importance.value = previous.importance || "";
    if (previous.size) size.value = previous.size;
    let column = previous.column ?? 4;
    let descending = previous.descending ?? true;
    let page = previous.page || 1;
    let pages = 1;
    const actions = ["first", "prev", "next", "last"].map((name) =>
        host.querySelector(`[data-analysis-${name}]`));

    /** Apply filters before ordering and pagination, retaining stable ties. */
    function render() {
        const query = search.value.trim().toLocaleLowerCase(locale);
        const selected = rows.filter((row) =>
            (!grade.value || row.dataset.grade === grade.value) &&
            (!importance.value || row.dataset.importance === importance.value) &&
            (!query || `${row.dataset.title} ${row.cells[1].textContent}`
                .toLocaleLowerCase(locale).includes(query)));
        selected.sort((left, right) =>
            compareAnalysisRows(left, right, column, descending, locale));
        const limit = size.value === "all" ? Math.max(1, selected.length) : Number(size.value);
        pages = Math.max(1, Math.ceil(selected.length / limit));
        page = Math.min(page, pages);
        const start = (page - 1) * limit;
        host.dataset.analysisColumn = String(column);
        host.dataset.analysisDescending = String(descending);
        host.dataset.analysisPage = String(page);
        rows.forEach((row) => { row.hidden = true; });
        selected.forEach((row, index) => {
            row.hidden = index < start || index >= start + limit;
            row.cells[0].textContent = String(index + 1);
            body.append(row);
        });
        result.textContent = host.dataset.resultCount
            .replace("{count}", String(selected.length)).replace("{total}", String(rows.length));
        pageStatus.textContent = host.dataset.pageStatus
            .replace("{start}", String(selected.length ? start + 1 : 0))
            .replace("{end}", String(Math.min(start + limit, selected.length)))
            .replace("{total}", String(selected.length));
        empty.hidden = selected.length !== 0;
        pager.hidden = selected.length <= limit;
        actions[0].disabled = actions[1].disabled = page <= 1;
        actions[2].disabled = actions[3].disabled = page >= pages;
        headings.forEach((heading, index) => {
            if (index) heading.setAttribute("aria-sort", index === column ?
                (descending ? "descending" : "ascending") : "none");
        });
    }
    [search, grade, importance, size].forEach((control) => {
        control.addEventListener(control === search ? "input" : "change", () => {
            page = 1;
            render();
        });
    });
    headings.forEach((heading, index) => {
        if (!index) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "ui-table__table__sort-button";
        button.textContent = heading.textContent;
        button.title = host.dataset.sortCaption.replace("{caption}", heading.textContent);
        button.addEventListener("click", () => {
            descending = column === index ? !descending : false;
            column = index;
            page = 1;
            render();
        });
        heading.replaceChildren(button);
    });
    actions.forEach((button, index) => button.addEventListener("click", () => {
        page = [1, page - 1, page + 1, pages][index];
        render();
    }));
    host.querySelector("[data-analysis-reset]").addEventListener("click", () => {
        search.value = grade.value = importance.value = "";
        page = 1;
        render();
        search.focus();
    });
    document.querySelectorAll("[data-analysis-grade]").forEach((link) => {
        link.addEventListener("click", (event) => {
            if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            grade.value = link.dataset.analysisGrade;
            page = 1;
            render();
        });
    });
    document.querySelectorAll("[data-analysis-importance]").forEach((link) => {
        link.addEventListener("click", (event) => {
            if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            importance.value = link.dataset.analysisImportance;
            page = 1;
            render();
        });
    });
    host.querySelector("[data-analysis-controls]").hidden = false;
    host.querySelector("[data-analysis-filter-note]").hidden = false;
    render();
}

/** Retain the current directory selection across a global B-Class update. */
function analysisState() {
    const host = document.querySelector("[data-quality-analysis]");
    if (!host) return {};
    const value = name => host.querySelector(`[data-analysis-${name}]`)?.value;
    return {
        search: value("search"), grade: value("grade-filter"),
        importance: value("importance-filter"), size: value("size"),
        column: Number(host.dataset.analysisColumn || 4),
        descending: host.dataset.analysisDescending !== "false",
        page: Number(host.dataset.analysisPage || 1),
    };
}

/** Replace the complete saved selection so the switch governs every section. */
export function initializeQualityAnalysisFilter() {
    const form = document.querySelector("[data-quality-analysis-filter]");
    const results = document.querySelector("[data-quality-results]");
    if (!form || !results || form.hasAttribute("data-auto-submit")) return;
    const input = form.querySelector('[name="count_b"]');
    const status = form.querySelector("[data-quality-analysis-status]");
    if (!input || !status) return;
    form.dataset.autoSubmit = "";
    if (typeof fetch !== "function" || typeof DOMParser !== "function") {
        input.addEventListener("change", () => form.requestSubmit());
        return;
    }
    let applied = input.checked;
    let sequence = 0;
    let controller;

    /** Apply only the latest valid same-origin saved-data response. */
    async function update(push) {
        const request = ++sequence;
        controller?.abort();
        controller = new AbortController();
        const selected = input.checked;
        const previous = analysisState();
        const url = new URL(form.action, location.href);
        url.search = new URLSearchParams(new FormData(form)).toString();
        results.setAttribute("aria-busy", "true");
        status.textContent = form.dataset.updating;
        try {
            const response = await fetch(url, {
                credentials: "same-origin", headers: {Accept: "text/html"},
                redirect: "error", signal: controller.signal,
            });
            if (!response.ok || !response.headers.get("content-type")?.includes("text/html"))
                throw new Error("Quality results unavailable");
            const page = new DOMParser().parseFromString(await response.text(), "text/html");
            if (request !== sequence) return;
            const incoming = page.querySelector("[data-quality-results]");
            if (!incoming || incoming.dataset.countB !== (selected ? "1" : "0") ||
                    incoming.querySelector("script, iframe, object, embed") ||
                    !incoming.querySelector("#analysis-overview") ||
                    !incoming.querySelector("#analysis-articles") ||
                    !incoming.querySelector("#quality-authors"))
                throw new Error("Invalid quality results");
            for (const dialog of results.querySelectorAll("dialog[open]")) dialog.close();
            results.replaceChildren(...incoming.childNodes);
            results.dataset.countB = incoming.dataset.countB;
            initializeQualityAnalysis(previous);
            initializeQualityAuthorship();
            applied = selected;
            if (push && url.href !== location.href) history.pushState(null, "", url);
            for (const link of document.querySelectorAll(".language-switcher a[hreflang]")) {
                const destination = new URL(link.href, location.href);
                if (selected) destination.searchParams.set("count_b", "1");
                else destination.searchParams.delete("count_b");
                link.href = destination.href;
            }
            status.textContent = "";
        } catch (error) {
            if (request !== sequence || error.name === "AbortError") return;
            input.checked = applied;
            status.textContent = form.dataset.updateFailed;
        } finally {
            if (request === sequence) results.removeAttribute("aria-busy");
        }
    }
    input.addEventListener("change", () => update(true));
    form.addEventListener("submit", event => {event.preventDefault(); update(true);});
    window.addEventListener("popstate", () => {
        const selected = new URL(location.href).searchParams.get("count_b") === "1";
        if (selected === applied) return;
        input.checked = selected;
        update(false);
    });
}

initializeQualityAnalysis();
initializeQualityAnalysisFilter();
if (document.querySelector("[data-quality-results]")) initializeQualityAuthorship();
