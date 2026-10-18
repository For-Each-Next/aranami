/** Verify local language filtering and sampling without network or DOM dependencies. */
"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { qualityIdentity, qualityPool, randomQualityItems } = require("../../src/toolforge/public_html/dashboard.js");

const records = Array.from({ length: 35 }, (_, index) => ({
  item: `Q${index + 1}`,
  site: index < 30 ? "enwiki" : "jawiki",
  language: index < 30 ? "en" : "ja",
  status: index % 2 ? "GA" : "FA",
  title: `Article ${index + 1}`,
}));

test("language selection retains every matching record and applies status independently", () => {
  assert.equal(qualityPool(records, "en").length, 30);
  assert.equal(qualityPool(records, "ja").length, 5);
  assert.equal(qualityPool(records, "en", "GA").length, 15);
  assert.equal(qualityPool(records, "missing").length, 0);
});

test("sampling returns ten distinct recognitions without mutating the full source", () => {
  const original = records.slice();
  const sample = randomQualityItems([...records, ...records], 10, () => 0.5);
  assert.equal(sample.length, 10);
  assert.equal(new Set(sample.map(qualityIdentity)).size, 10);
  assert.deepEqual(records, original);
});

test("samples retain the selected language and return the entire smaller pool", () => {
  const sample = randomQualityItems(qualityPool(records, "ja"), 10, () => 0);
  assert.equal(sample.length, 5);
  assert(sample.every((item) => item.language === "ja"));
  assert.deepEqual(randomQualityItems([]), []);
});

test("different recognitions and sites for one subject keep their separate identities", () => {
  const item = records[0];
  const pool = qualityPool([item, { ...item }, { ...item, status: "GA" }, { ...item, site: "dewiki" }]);
  assert.equal(pool.length, 3);
});

test("the random selector can reach the end of the complete collection", () => {
  const sample = randomQualityItems(records, 10, () => 0.999999);
  assert.equal(sample[0], records.at(-1));
  assert.equal(sample.length, 10);
});
