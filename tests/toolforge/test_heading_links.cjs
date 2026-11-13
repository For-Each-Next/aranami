/* Verify stable language-independent heading targets and literal wikilink labels. */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../../src/webservice/public_html/heading_links.js"), "utf8").replace(/export \{[\s\S]*?\};?\s*$/, "");
const context = {URLSearchParams};
vm.runInNewContext(source, context);
const {headingWikilink, pageWikilink} = context;

test("every interface language copies the same ASCII section destination", () => {
    const expected = "[[toolforge:aranami/about/#server-status-heading|";
    for (const label of ["Server status", "服务器状态", "伺服器狀態"]) {
        const result = headingWikilink("/about/", "server-status-heading", label);
        assert.equal(result, `${expected}${label}]]`);
    }
    const paths = ["https://aranami.toolforge.org/about/?lang=en", "https://aranami.toolforge.org/about/?lang=zh-hans", "https://aranami.toolforge.org/about/?lang=zh-hant#credits"];
    assert.equal(new Set(paths.map(url => headingWikilink(new URL(url).pathname, "credits", "Credits"))).size, 1);
});

test("functional selectors survive while language-only choices are removed", () => {
    const first = headingWikilink('/maintenance/', 'maintenance-results', 'Results', '?lang=en&task=references&q=A%20%7C%20%5B%5BB%5D%5D&issue=2026-11&date=2026-11-09&page=2');
    const second = headingWikilink('/maintenance/', 'maintenance-results', 'Results', '?task=references&q=A%20%7C%20%5B%5BB%5D%5D&issue=2026-11&date=2026-11-09&page=2&lang=zh-hant');
    assert.equal(first, second);
    assert.equal(first, '[[toolforge:aranami/maintenance/?task=references&q=A+%7C+%5B%5BB%5D%5D&issue=2026-11&date=2026-11-09&page=2#maintenance-results|Results]]');
    assert.equal(first.includes('lang='), false);
    assert.equal(headingWikilink('/newsletter/', 'newsletter-drafts', 'Drafts', '?lang=en&lang=zh-hant'), '[[toolforge:aranami/newsletter/#newsletter-drafts|Drafts]]');
});

test("root and nested routes preserve their actual stable destination", () => {
    assert.equal(headingWikilink("/", "reviews-heading", "Reviews"), "[[toolforge:aranami/#reviews-heading|Reviews]]");
    assert.equal(headingWikilink("/assets/icons/credits.html", "attribution-images", "Images"), "[[toolforge:aranami/assets/icons/credits.html#attribution-images|Images]]");
});

test("page headings copy the selected route without a fragment or language", () => {
    assert.equal(pageWikilink("/newsletter/", "简讯草稿", "?lang=zh-hans"), "[[toolforge:aranami/newsletter/|简讯草稿]]");
    assert.equal(pageWikilink("/newsletter/", "Drafts", "?issue=2026-11&lang=en&date=2026-11-09"), "[[toolforge:aranami/newsletter/?issue=2026-11&date=2026-11-09|Drafts]]");
    assert.equal(pageWikilink("/", "A | [[B]]"), "[[toolforge:aranami/|A &#124; &#91;&#91;B&#93;&#93;]]");
    assert.throws(() => pageWikilink("https://example.org/", "Page"));
    assert.throws(() => pageWikilink("//example.org/", "Page"));
});

test("labels cannot close links, invoke templates, create markup or add lines", () => {
    const label = " A | [[B]] & <script> {{C}} ''D''\n second\r\nline ";
    const result = headingWikilink("/about/", "credits", label);
    assert.equal(result, "[[toolforge:aranami/about/#credits|A &#124; &#91;&#91;B&#93;&#93; &amp; &lt;script&gt; &#123;&#123;C&#125;&#125; &#39;&#39;D&#39;&#39; second line]]");
    assert.equal(result.includes("\n"), false);
});

test("path encoding preserves escaped separators and blocks wikilink syntax", () => {
    assert.equal(headingWikilink("/a%2Fb/", "credits", "A"), "[[toolforge:aranami/a%2Fb/#credits|A]]");
    assert.equal(headingWikilink("/a|b]]/{{x}}/'/", "credits", "A"), "[[toolforge:aranami/a%7Cb%5D%5D/%7B%7Bx%7D%7D/%27/#credits|A]]");
    assert.equal(headingWikilink("/bad%escape/", "credits", "A"), "[[toolforge:aranami/bad%25escape/#credits|A]]");
});

test("untrusted or unstable fragments and nonlocal paths are rejected", () => {
    for (const fragment of ["", "Heading text", "伺服器狀態", "x|unsafe", "x]]", "../x"])
        assert.throws(() => headingWikilink("/about/", fragment, "Heading"));
    assert.throws(() => headingWikilink("https://example.org/about/", "credits", "Heading"));
    assert.throws(() => headingWikilink("//example.org/about/", "credits", "Heading"));
});
