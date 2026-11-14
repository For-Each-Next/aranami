/** Show evidence coverage and bounded lists for the selected article cohort. */
import { qualityMetadata } from "./quality_matrix.js";

const pageSize = 50;
let cohortListenerInstalled = false;

/** Preserve measured zeroes and acquired attribution without named editors. */
export function articleCoverage(record) {
    const article = record.article;
    return {
        authorship: article ? article.authorship?.available === true : record.dataset.coverageAuthorship === "1",
        dates: article ? Boolean(article.listed_date) : Boolean(record.dataset.year),
        length: article ? article.prose_characters !== null && article.prose_characters !== undefined : record.dataset.measured === "1",
    };
}

/** Count only matrix-selected records, independently of directory searching. */
export function coverageSummary(records) {
    const counts = {total: 0, authorship: 0, dates: 0, length: 0};
    for (const record of records) {
        if (record.dataset.cohort === "0") continue;
        counts.total++;
        for (const [key, acquired] of Object.entries(articleCoverage(record))) counts[key] += Number(acquired);
    }
    return counts;
}

/** Include either source's selected grade, retaining category-only articles. */
export function comparisonSelection(comparison, grades) {
    if (!comparison?.available) return [];
    return (comparison.rows || []).filter(row =>
        grades.has(row.pageassessments_grade) || row.category_grades.some(grade => grades.has(grade)));
}

/** Reuse lightweight catalog records and keep only one dialog page in the DOM. */
export function initializeQualityCoverage() {
    const host = document.querySelector("[data-quality-coverage]");
    const dialog = document.getElementById("analysis-coverage-dialog");
    if (!host || !dialog || host.coverageRefresh) return;
    const labels = JSON.parse(host.dataset.coverageLabels);
    const body = dialog.querySelector("[data-coverage-rows]");
    const empty = dialog.querySelector("[data-coverage-empty]");
    const status = dialog.querySelector("[data-coverage-page-status]");
    const table = body.closest("table");
    const pager = status.closest("nav");
    let selected = [], page = 1, kind, opener;
    const number = value => value.toLocaleString(document.documentElement.lang);
    const records = () => document.querySelector("[data-quality-analysis]")?.analysisRows || [];
    const metadata = () => qualityMetadata();
    const comparison = () => metadata()?.assessment_comparison ||
        (host.dataset.coverageComparison ? JSON.parse(host.dataset.coverageComparison) : null);
    const grades = () => {
        const form = document.querySelector("[data-quality-matrix]");
        return new Set(form ? [...form.querySelectorAll("[data-matrix-cell]")]
            .filter(input => input.checked).map(input => input.dataset.grade) : records().map(row => row.dataset.grade));
    };
    const node = (tag, text) => {
        const element = document.createElement(tag);
        if (text !== undefined) element.textContent = String(text);
        return element;
    };
    const gradeLabel = grade => metadata()?.grades.find(item => item.value === grade)?.article_label || grade || "—";

    /** Retain references only to the currently requested missing-page list. */
    function chooseRows() {
        selected = kind === "total" ? comparisonSelection(comparison(), grades()) : records().filter(record =>
            record.dataset.cohort !== "0" && !articleCoverage(record)[kind]);
    }

    /** Replace one fifty-row page and its bounded navigation controls. */
    function render() {
        const pages = Math.max(1, Math.ceil(selected.length / pageSize));
        page = Math.max(1, Math.min(page, pages));
        const start = (page - 1) * pageSize;
        const visible = selected.slice(start, start + pageSize);
        body.replaceChildren(...visible.map((record, index) => {
            const row = node("tr"), titleCell = node("td");
            const title = kind === "total" ? record.title : record.article?.title || record.dataset.title;
            const label = kind === "total" ? title.replaceAll("_", " ") : record.article?.title_label || title.replaceAll("_", " ");
            const link = node("a", label);
            link.href = `https://zh.wikipedia.org/wiki/${encodeURIComponent(title.replaceAll(" ", "_"))}`;
            titleCell.append(link);
            row.append(node("td", number(start + index + 1)), titleCell);
            if (kind === "total") row.append(
                node("td", record.category_grades.map(gradeLabel).join(" / ") || "—"),
                node("td", gradeLabel(record.pageassessments_grade)),
                node("td", labels[record.kind] || record.kind));
            return row;
        }));
        for (const heading of dialog.querySelectorAll("[data-coverage-comparison-column]")) heading.hidden = kind !== "total";
        empty.hidden = selected.length > 0;
        empty.textContent = kind === "total" && !comparison()?.available ? labels.comparison_unavailable : labels.no_missing;
        table.hidden = pager.hidden = selected.length === 0;
        status.textContent = status.dataset.template.replace("{start}", number(selected.length ? start + 1 : 0))
            .replace("{end}", number(start + visible.length)).replace("{total}", number(selected.length));
        for (const button of dialog.querySelectorAll("[data-coverage-page]")) button.disabled =
            ["first", "previous"].includes(button.dataset.coveragePage) ? page === 1 : page === pages;
    }

    host.coverageRefresh = () => {
        const counts = coverageSummary(records());
        for (const element of host.querySelectorAll("[data-coverage-count]")) element.textContent = number(counts[element.dataset.coverageCount]);
        if (dialog.open) {chooseRows(); page = 1; render();}
    };
    host.addEventListener("click", event => {
        const control = event.target.closest("[data-coverage-open]");
        if (!control || event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0 || typeof dialog.showModal !== "function") return;
        event.preventDefault();
        kind = control.dataset.coverageOpen;
        opener = control;
        dialog.querySelector(".ui-dialog__header__title").textContent = labels[kind === "total" ? "comparison" : `missing_${kind}`];
        chooseRows(); page = 1; render(); dialog.showModal();
    });
    dialog.addEventListener("click", event => {
        const control = event.target.closest("[data-coverage-page]");
        if (!control) return;
        const target = control.dataset.coveragePage;
        page = target === "first" ? 1 : target === "last" ? Math.ceil(selected.length / pageSize) : page + (target === "next" ? 1 : -1);
        render();
        dialog.querySelector(".ui-dialog__body")?.scrollTo(0, 0);
    });
    dialog.addEventListener("close", () => {
        selected = []; body.replaceChildren();
        const returnFocus = opener;
        opener = kind = undefined;
        if (returnFocus?.isConnected) returnFocus.focus();
    });
    if (!cohortListenerInstalled) {
        document.addEventListener("quality-cohort-change", () => document.querySelector("[data-quality-coverage]")?.coverageRefresh?.());
        cohortListenerInstalled = true;
    }
    host.coverageRefresh();
}
