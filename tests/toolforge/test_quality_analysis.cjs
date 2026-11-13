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
    .replace(/^import .*;$/m, "")
    .replaceAll("export function", "function");
const context = { document: { querySelector: () => null } };
vm.runInNewContext(shared + "\n" + analysis, context);
const compare = context.compareAnalysisRows;
const row = (column, value) => ({
    cells: Array.from({ length: 7 }, (_, index) => ({ dataset: index === column ? { sort: value } : {} })),
});

test("numeric prose and source sizes sort as observations, including measured zero", () => {
    for (const column of [2, 3, 5, 6]) {
        assert.ok(compare(row(column, "9"), row(column, "100"), column, false, "en") < 0);
        assert.ok(compare(row(column, "0"), row(column, "9"), column, false, "en") < 0);
        assert.ok(compare(row(column, "9"), row(column, "100"), column, true, "en") > 0);
    }
});

test("missing dates and measurements stay last in either direction", () => {
    for (const column of [4, 5, 6]) {
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
