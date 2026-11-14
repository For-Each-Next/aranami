/* Check article sorting against saved numerical, zero and unknown facts. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const root = path.join(__dirname, "../../src/webservice/public_html");
const shared = fs.readFileSync(path.join(root, "quality_authorship.js"), "utf8")
    .replaceAll("export function", "function");
const analysis = fs.readFileSync(path.join(root, "quality_analysis.js"), "utf8")
    .replace(/^import .*;$/gm, "")
    .replaceAll("export function", "function");
const context = { document: { querySelector: () => null } };
const matrix = fs.readFileSync(path.join(root, "quality_matrix.js"), "utf8").replaceAll("export function", "function");
vm.runInNewContext(shared + "\n" + matrix + "\n" + analysis, context);
const compare = context.compareAnalysisRows;
const row = (column, value) => ({
    cells: Array.from({ length: 8 }, (_, index) => ({ dataset: index === column ? { sort: value } : {} })),
});

test("numeric prose and source sizes sort as observations, including measured zero", () => {
    for (const column of [2, 3, 6, 7]) {
        assert.ok(compare(row(column, "9"), row(column, "100"), column, false, "en") < 0);
        assert.ok(compare(row(column, "0"), row(column, "9"), column, false, "en") < 0);
        assert.ok(compare(row(column, "9"), row(column, "100"), column, true, "en") > 0);
    }
});

test("missing dates and measurements stay last in either direction", () => {
    for (const column of [4, 6, 7]) {
        for (const descending of [true, false]) {
            assert.ok(compare(row(column, ""), row(column, "1"), column, descending, "en") > 0);
            assert.equal(compare(row(column, ""), row(column, ""), column, descending, "en"), 0);
        }
    }
});

test("listing dates use ISO order and identical titles remain stable", () => {
    assert.ok(compare(row(4, "2026-01-01"), row(4, "2025-12-31"), 4, true, "zh-Hant") < 0);
    assert.equal(compare(row(1, "遊戲"), row(1, "遊戲"), 1, false, "zh-Hant"), 0);
});


test("contributor names sort as text while missing attribution remains last", () => {
    assert.ok(compare(row(5, "Author A"), row(5, "Author B"), 5, false, "en") < 0);
    assert.ok(compare(row(5, ""), row(5, "Author A"), 5, false, "en") > 0);
    assert.ok(compare(row(5, ""), row(5, "Author A"), 5, true, "en") > 0);
});


test("chart selections match the exact year and grade independently of table visibility", () => {
    const matches = context.chartArticleMatches;
    const selection = {chartGroup: "grade", chartValue: "B", chartYear: "2026"};
    assert.equal(matches({hidden: true, dataset: {grade: "B", year: "2026"}}, selection), true);
    assert.equal(matches({dataset: {grade: "B", year: "2025"}}, selection), false);
    assert.equal(matches({dataset: {grade: "BL", year: "2026"}}, selection), false);
    assert.equal(matches({dataset: {grade: "B", year: ""}}, selection), false);
    assert.equal(matches({dataset: {grade: "B", measured: "0"}}, {chartGroup: "grade", chartValue: "B", chartMeasured: "1"}), false);
});


test("year ranges include both endpoints and independently include unknown dates", () => {
    assert.equal(context.yearSelected(2002, 2002, 2026, false), true);
    assert.equal(context.yearSelected("2026", 2002, 2026, false), true);
    assert.equal(context.yearSelected(2027, 2002, 2026, true), false);
    assert.equal(context.yearSelected(null, 2002, 2026, true), true);
    assert.equal(context.yearSelected("", 2002, 2026, false), false);
});

test("saved cells combine exact quantiles without treating unknown measurements as zero", () => {
    const combined = context.combineCells([
        {pages: 3, measured: 2, histogram: [{value: 0, count: 1}, {value: 10, count: 1}], density: [{position: 0, density: .2}, {position: 10, density: .1}]},
        {pages: 1, measured: 1, histogram: [{value: 20, count: 1}], density: [{position: 0, density: .1}, {position: 10, density: .5}]},
    ]);
    assert.equal(combined.pages, 4);
    assert.equal(combined.measured, 3);
    assert.equal(combined.quantiles[2], 10);
    assert.equal(combined.density[1][1], 1);
    assert.ok(Math.abs(combined.density[0][1] - .5) < 1e-12);
    assert.equal(context.combineCells([]).quantiles[2], null);
});
