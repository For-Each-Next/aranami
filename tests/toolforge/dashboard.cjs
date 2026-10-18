/** Verify local language filtering and sampling without network or DOM dependencies. */
"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { qualityIdentity, qualityHasLocal, qualityPool, randomQualityItems, qualityViewItems } = require("../../src/toolforge/public_html/dashboard.js");

const records = Array.from({ length: 35 }, (_, index) => ({
  item: `Q${index + 1}`,
  site: index < 30 ? "enwiki" : "jawiki",
  language: index < 30 ? "en" : "ja",
  status: index % 2 ? "GA" : "FA",
  title: `Article ${index + 1}`,
  zh_title: index % 3 === 0 ? `Chinese ${index + 1}` : null,
  zh_url: index % 3 === 0 ? `https://zh.wikipedia.org/wiki/Chinese_${index + 1}` : null,
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

test("display all expands initial recommendations to the entire saved collection", () => {
  const data = { items: records, defaults: { "": records.slice(0, 10) } };
  assert.equal(qualityViewItems(data, { language: "", status: "", random: false }).length, 10);
  assert.equal(qualityViewItems(data, { language: "", status: "", all: true }).length, 35);
  assert.equal(qualityViewItems(data, { language: "", status: "", all: true, random: true }).length, 35);
});

test("complete selections retain status and language limits without the ten-record cap", () => {
  const data = { items: records, defaults: {} };
  const filtered = qualityViewItems(data, { language: "en", status: "GA", all: true });
  assert.equal(filtered.length, 15);
  assert(filtered.every((item) => item.language === "en" && item.status === "GA"));
  const allLanguages = qualityViewItems(data, { language: "", status: "GA", all: true });
  assert.equal(allLanguages.length, 17);
});

test("local existence requires a complete counterpart or the Chinese article itself", () => {
  assert.equal(qualityHasLocal({ language: "zh", zh_title: null, zh_url: null }), true);
  assert.equal(qualityHasLocal(records[0]), true);
  assert.equal(qualityHasLocal(records[1]), false);
  assert.equal(qualityHasLocal({ language: "en", zh_title: "Chinese", zh_url: null }), false);
  assert.equal(qualityHasLocal({ language: "en", zh_title: null, zh_url: "https://zh.wikipedia.org/wiki/Chinese" }), false);
  assert.equal(qualityHasLocal({ language: "en", zh_title: "", zh_url: "https://zh.wikipedia.org/wiki/Chinese" }), false);
});

test("local filters combine with language and recognition filters", () => {
  assert.equal(qualityPool(records, "", "", "exists").length, 12);
  assert.equal(qualityPool(records, "", "", "missing").length, 23);
  const filtered = qualityPool(records, "en", "GA", "missing");
  assert.equal(filtered.length, 10);
  assert(filtered.every((item) => item.language === "en" && item.status === "GA" && !qualityHasLocal(item)));
  const chinese = { ...records[1], language: "zh", site: "zhwiki" };
  assert(qualityPool([chinese], "zh", "", "exists").includes(chinese));
  assert.deepEqual(qualityPool([chinese], "zh", "", "missing"), []);
});

test("an explicit local filter opens the complete pool without the default ten-record cap", () => {
  const data = { items: records, defaults: { "": records.slice(0, 10) } };
  assert.equal(qualityViewItems(data, { language: "", status: "", local: "missing" }).length, 23);
  assert.equal(qualityViewItems(data, { language: "en", status: "", local: "missing" }).length, 20);
  assert.equal(qualityViewItems(data, { language: "", status: "", local: "exists", random: true }).length, 12);
  assert.equal(qualityViewItems(data, { language: "", status: "", local: "invalid" }).length, 10);
});
