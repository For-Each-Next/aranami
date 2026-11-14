/* Verify truthful coverage and bidirectional, grade-selected comparisons. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../src/webservice/public_html/quality_coverage.js"), "utf8")
    .replace(/^import .*;$/gm, "").replaceAll("export function", "function");
const context = {};
vm.runInNewContext(source, context);

test("coverage distinguishes measured zero and acquired attribution without contributors", () => {
    const rows = [
        {dataset: {cohort: "1"}, article: {authorship: {available: true, editors: []}, listed_date: "2026-01-01", prose_characters: 0}},
        {dataset: {cohort: "1", coverageAuthorship: "0", year: "", measured: "0"}},
        {dataset: {cohort: "1", coverageAuthorship: "1", year: "2025", measured: "1"}},
        {dataset: {cohort: "0", coverageAuthorship: "1", year: "2025", measured: "1"}},
    ];
    assert.equal(JSON.stringify(context.coverageSummary(rows)), JSON.stringify({total: 3, authorship: 2, dates: 2, length: 2}));
});

test("comparison includes either side's selected grade and keeps multiple categories", () => {
    const comparison = {available: true, rows: [
        {page_id: 1, category_grades: ["FA"], pageassessments_grade: null, kind: "only_categories"},
        {page_id: 2, category_grades: [], pageassessments_grade: "FA", kind: "only_pageassessments"},
        {page_id: 3, category_grades: ["GA", "FA"], pageassessments_grade: "B", kind: "grade_mismatch"},
        {page_id: 4, category_grades: ["B"], pageassessments_grade: "FA", kind: "grade_mismatch"},
        {page_id: 5, category_grades: ["B"], pageassessments_grade: null, kind: "only_categories"},
    ]};
    const selected = context.comparisonSelection(comparison, new Set(["FA"]));
    assert.deepEqual(Array.from(selected, row => row.page_id), [1, 2, 3, 4]);
    assert.deepEqual(selected[2].category_grades, ["GA", "FA"]);
    assert.equal(context.comparisonSelection(comparison, new Set()).length, 0);
    assert.equal(context.comparisonSelection({available: false, rows: comparison.rows}, new Set(["FA"])).length, 0);
});
