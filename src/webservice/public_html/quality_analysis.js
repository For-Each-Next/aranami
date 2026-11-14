/** Filter and sort the complete saved article list without source requests. */
import { compareValues, initializeQualityAuthorship } from "./quality_authorship.js";
import { initializeQualityCoverage } from "./quality_coverage.js";
import { initializeQualityChartControls } from "./quality_chart_controls.js";
import { initializeQualityMatrix, initializeQualitySelection, prepareCatalogRows, catalogRows, articleElement, qualityMetadata, loadQualityMetadata } from "./quality_matrix.js";

/** Keep the prepared overview usable while its full catalog loads after paint. */
export function initializeQualityPage(previous = {}) {
    if (typeof initializeQualityChartControls === "function") initializeQualityChartControls();
    const form = document.querySelector("[data-quality-matrix]");
    const complete = () => {
        initializeQualityAnalysis(previous);
        initializeQualityMatrix();
        if (document.querySelector("[data-quality-coverage]")) initializeQualityCoverage();
        if (document.querySelector("[data-quality-results]")) initializeQualityAuthorship();
        if (form) form.dataset.qualityReady = "1";
    };
    if (!form) {complete(); return;}
    initializeQualitySelection(form);
    if ((!qualityMetadata(form) && !form.dataset.metadataUrl) || form.hasAttribute("data-quality-loading")) return;
    form.setAttribute("data-quality-loading", "");
    const status = form.querySelector("[data-quality-load-status]");
    const retry = form.querySelector("[data-quality-retry]");
    const loading = form.dataset.loading || "Loading article filters…";
    const failure = form.dataset.loadFailed || "Article filters could not load. Try again.";
    if (status) {status.textContent = loading; status.hidden = false;}
    if (retry) retry.hidden = true;
    // The visible author directory is small; its closed detail tables stay lazy.
    if (document.querySelector("[data-quality-results]")) initializeQualityAuthorship();
    let pending = false;
    async function load() {
        if (pending || !form.isConnected || form.dataset.qualityReady === "1") return;
        pending = true;
        form.dataset.qualityLoadState = "loading";
        if (status) status.textContent = loading;
        if (retry) retry.hidden = true;
        try {
            await loadQualityMetadata(form);
            await prepareCatalogRows(form);
            if (!form.isConnected) return;
            complete();
            form.dataset.qualityLoadState = "ready";
            if (status) status.hidden = true;
        } catch {
            if (!form.isConnected) return;
            form.dataset.qualityLoadState = "error";
            if (status) {status.textContent = failure; status.hidden = false;}
            if (retry) retry.hidden = false;
        } finally {pending = false;}
    }
    retry?.addEventListener("click", () => void load());
    // Two frames let the initial HTML and charts paint before catalog processing.
    if (qualityMetadata(form)) void load();
    else requestAnimationFrame(() => requestAnimationFrame(() => void load()));
}

/** Compare existing cell observations while leaving unknown values last. */
export function compareAnalysisRows(left, right, column, descending, locale) {
    const read = (row) => {
        const value = row.cells[column].dataset.sort;
        return value === undefined || value === "" ? null :
            [2, 3, 6, 7].includes(column) ? Number(value) : value;
    };
    return compareValues(read(left), read(right), descending, locale);
}

/** Read only explicit main-contributor and proven significant-contributor names. */
export function articleContributorRoles(row) {
    return {
        main: JSON.parse(row.dataset.mainAuthors || "[]"),
        mainLabels: JSON.parse(row.dataset.mainAuthorLabels || row.dataset.mainAuthors || "[]"),
        significant: JSON.parse(row.dataset.significantAuthors || "[]"),
    };
}

/** Match an author only within the selected contribution type. */
export function contributorMatches(roles, author, role, locale) {
    const name = author.trim().toLocaleLowerCase(locale);
    return !name || [...(roles[role] || []), ...(role === "main" ? roles.mainLabels || [] : [])]
        .some(value => value.toLocaleLowerCase(locale) === name);
}

/** Enhance the native table while preserving its escaped cells and links. */
export function initializeQualityAnalysis(previous = {}) {
    const host = document.querySelector("[data-quality-analysis]");
    if (!host || host.hasAttribute("data-analysis-enhanced")) return;
    host.setAttribute("data-analysis-enhanced", "");
    const rows = [...host.querySelectorAll("[data-analysis-row]"), ...catalogRows()];
    host.analysisRows = rows;
    const table = host.querySelector("table");
    const body = table.tBodies[0];
    const headings = [...table.querySelectorAll("thead th")];
    const search = host.querySelector("[data-analysis-search]");
    const grade = host.querySelector("[data-analysis-grade-filter]");
    const importance = host.querySelector("[data-analysis-importance-filter]");
    const author = host.querySelector("[data-analysis-author]");
    const role = host.querySelector("[data-analysis-author-role]");
    const contributors = new Map(rows.map(row => [row, articleContributorRoles(row)]));
    const suggestions = host.querySelector("#analysis-author-names");
    function suggestContributors() {
        if (!suggestions) return;
        const names = [...new Set([...contributors.values()].flatMap(items => items[role?.value === "significant" ? "significant" : "mainLabels"] || []))].sort();
        suggestions.replaceChildren(...names.map(name => {const option = document.createElement("option"); option.value = name; return option;}));
    }
    const size = host.querySelector("[data-analysis-size]");
    const pager = host.querySelector("[data-analysis-pager]");
    const result = host.querySelector("[data-analysis-result-count]");
    const pageStatus = host.querySelector("[data-analysis-page-status]");
    const empty = host.querySelector("[data-analysis-no-matches]");
    const locale = document.documentElement.lang;
    search.value = previous.search ?? search.value;
    grade.value = previous.grade ?? grade.value;
    importance.value = previous.importance ?? importance.value;
    if (author) author.value = previous.author ?? author.value;
    if (role) role.value = ["main", "significant"].includes(previous.role) ? previous.role : "main";
    suggestContributors();
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
            (row.dataset.cohort !== "0") &&
            contributorMatches(contributors.get(row), author?.value || "", role?.value || "main", locale) &&
            (!grade.value || row.dataset.grade === grade.value) &&
            (!importance.value || row.dataset.importance === importance.value) &&
            (!query || `${row.dataset.title} ${row.cells[1].dataset.sort}`
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
        body.replaceChildren(...selected.slice(start, start + limit).map((record, index) => {
            const row = articleElement(record); row.hidden = false;
            row.cells[0].textContent = String(start + index + 1);
            Object.assign(row.dataset, record.dataset);
            return row;
        }));
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
    [search, grade, importance, size, author, role].filter(Boolean).forEach((control) => {
        control.addEventListener(control === search || control === author ? "input" : "change", () => {
            if (control === role) suggestContributors();
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
        if (author) author.value = "";
        if (role) role.value = "main";
        suggestContributors();
        page = 1;
        render();
        search.focus();
    });
    const cohortChanged = () => {
        if (!host.isConnected) {
            document.removeEventListener("quality-cohort-change", cohortChanged);
            return;
        }
        page = 1;
        render();
    };
    document.addEventListener("quality-cohort-change", cohortChanged);
    initializeChartArticles(rows);
    host.querySelector("[data-analysis-controls]").hidden = false;
    render();
}

/** Select a chart's saved cohort independently of directory filters. */
export function chartArticleMatches(row, selection) {
    return row.dataset.cohort !== "0" && row.dataset[selection.chartGroup] === selection.chartValue &&
        (!selection.chartYear || row.dataset.year === selection.chartYear) &&
        (!selection.chartMeasured || row.dataset.measured === "1");
}

/** Sort chart-dialog columns against their original article observations. */
export function orderChartArticles(rows, column, descending, locale) {
    const sourceColumn = [0, 1, 2, 3, 4, 6][column];
    return [...rows].sort((left, right) => compareAnalysisRows(left, right, sourceColumn, descending, locale));
}

/** Fill one accessible dialog from already rendered, escaped article cells. */
function initializeChartArticles(rows) {
    const dialog = document.getElementById("analysis-chart-articles");
    if (!dialog || typeof dialog.showModal !== "function") return;
    const body = dialog.querySelector("[data-chart-article-rows]");
    const count = dialog.querySelector("[data-chart-article-count]");
    const heading = document.getElementById("analysis-chart-articles-heading");
    const pager = dialog.querySelector("[data-chart-pager]");
    const sizeControl = pager.querySelector("[data-chart-size]");
    const headings = [...dialog.querySelectorAll("thead th")];
    const sortCaption = document.querySelector("[data-quality-analysis]")?.dataset.sortCaption;
    let selected = [], page = 1;
    let column = 4, descending = true;
    const pageCount = () => Math.max(1, Math.ceil(selected.length / Number(sizeControl.value)));
    const actions = {first: () => 1, prev: () => page - 1, next: () => page + 1, last: pageCount};
    function render() {
        const size = Number(sizeControl.value), pages = pageCount();
        page = Math.max(1, Math.min(page, pages));
        const start = (page - 1) * size;
        const ordered = orderChartArticles(selected, column, descending, document.documentElement.lang);
        body.replaceChildren(...ordered.slice(start, start + size).map((source, index) => {
            const row = document.createElement("tr");
            row.dataset.chartArticle = source.dataset.title;
            const original = articleElement(source);
            for (const column of [0, 1, 2, 3, 4, 6]) {
                const cell = original.cells[column].cloneNode(true);
                if (!column) cell.textContent = String(start + index + 1);
                row.append(cell);
            }
            return row;
        }));
        pager.querySelector("[data-chart-page-status]").textContent = pager.dataset.pageStatus
            .replace("{start}", String(selected.length ? start + 1 : 0))
            .replace("{end}", String(Math.min(start + size, selected.length)))
            .replace("{total}", String(selected.length));
        for (const key of Object.keys(actions)) pager.querySelector(`[data-chart-${key}]`).disabled = ["first", "prev"].includes(key) ? page === 1 : page === pages;
        headings.forEach((heading, index) => {
            if (index) heading.setAttribute("aria-sort", index === column ? (descending ? "descending" : "ascending") : "none");
        });
    }
    headings.forEach((heading, index) => {
        if (!index) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "ui-table__table__sort-button";
        button.textContent = heading.textContent;
        button.title = sortCaption?.replace("{caption}", heading.textContent) || heading.textContent;
        button.addEventListener("click", () => {
            descending = column === index ? !descending : false;
            column = index;
            page = 1;
            render();
        });
        heading.replaceChildren(button);
    });
    for (const [key, destination] of Object.entries(actions)) pager.querySelector(`[data-chart-${key}]`).addEventListener("click", () => {page = destination(); render();});
    sizeControl.addEventListener("change", () => {page = 1; render();});
    const openChart = event => {
        if (!dialog.isConnected) {
            document.removeEventListener("click", openChart);
            return;
        }
        const link = event.target.closest?.("[data-chart-group]");
        if (!link || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        event.stopPropagation();
        selected = rows.filter(row => chartArticleMatches(row, link.dataset));
        page = 1;
        column = 4;
        descending = true;
        render();
        heading.textContent = link.dataset.chartLabel;
        count.textContent = count.dataset.label.replace("{count}", String(selected.length));
        link.focus();
        if (!dialog.open) dialog.showModal();
    };
    document.addEventListener("click", openChart);
    const activateChart = event => {
        if (!dialog.isConnected) {
            document.removeEventListener("keydown", activateChart);
            return;
        }
        const link = event.target.closest?.("[data-chart-group]");
        if (!link || event.key !== " ") return;
        event.preventDefault();
        link.dispatchEvent(new MouseEvent("click", {bubbles: true}));
    };
    document.addEventListener("keydown", activateChart);
}

/** Retain the current directory selection across a global B-Class update. */
function analysisState() {
    const host = document.querySelector("[data-quality-analysis]");
    if (!host) return {};
    const value = name => host.querySelector(`[data-analysis-${name}]`)?.value;
    return {
        search: value("search"), grade: value("grade-filter"),
        importance: value("importance-filter"), size: value("size"),
        author: value("author"), role: value("author-role"),
        column: Number(host.dataset.analysisColumn || 4),
        descending: host.dataset.analysisDescending !== "false",
        page: Number(host.dataset.analysisPage || 1),
    };
}

document.addEventListener?.("quality-charts-replaced", () => initializeQualityChartControls());
initializeQualityPage();
const basis = document.querySelector("[data-authorship-basis]");
if (basis) basis.querySelector("input[type=checkbox]").addEventListener("change", () => basis.requestSubmit());
