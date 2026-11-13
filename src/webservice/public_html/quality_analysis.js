/** Filter and sort the complete saved article list without source requests. */
import { compareValues } from "./quality_authorship.js";

/** Compare existing cell observations while leaving unknown values last. */
export function compareAnalysisRows(left, right, column, descending, locale) {
    const read = (row) => {
        const value = row.cells[column].dataset.sort;
        return value === undefined || value === "" ? null :
            [2, 3, 5, 6].includes(column) ? Number(value) : value;
    };
    return compareValues(read(left), read(right), descending, locale);
}

/** Enhance the native table while preserving its escaped cells and links. */
export function initializeQualityAnalysis() {
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
    let column = 4;
    let descending = true;
    let page = 1;
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

initializeQualityAnalysis();
