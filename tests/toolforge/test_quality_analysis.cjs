/* Check article sorting against saved numerical, zero and unknown facts. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const root = path.join(__dirname, "../../src/webservice/public_html");
const interfaceMessages = JSON.parse(fs.readFileSync(path.join(root, "../messages.json"), "utf8"));
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

function loaderContext(overrides = {}) {
    const context = {
        document: {querySelector: () => null, getElementById: () => null},
        location: {href: "https://aranami.example/quality-analysis/", origin: "https://aranami.example"},
        URL, AbortController, setTimeout, clearTimeout, Event,
        ...overrides,
    };
    vm.runInNewContext(shared + "\n" + matrix + "\n" + analysis, context);
    return context;
}

function catalog() {
    return {cells: [], extra: [], grades: [], importance: [], labels: {}};
}

function response(body, type = "application/json") {
    return {ok: true, headers: {get: () => type}, json: async () => body, text: async () => body};
}

test("catalog requests share one fetch and one parsed object across controls", async () => {
    let finish, calls = 0, parses = 0;
    const context = loaderContext({fetch: () => {
        calls++;
        return new Promise(resolve => {finish = resolve;});
    }});
    const form = {dataset: {metadataUrl: "/quality-analysis/resources/catalog-version.json", authorshipIcon: "/icon.svg"}};
    const first = context.loadQualityMetadata(form);
    const second = context.loadQualityMetadata(form);
    assert.equal(first, second);
    const data = catalog();
    finish({...response(data), json: async () => {parses++; return data;}});
    assert.equal(await first, data);
    assert.equal(context.qualityMetadata(form), data);
    assert.equal(await context.loadQualityMetadata(form), data);
    assert.equal(calls, 1);
    assert.equal(parses, 1);
    assert.equal(data.authorshipIcon, "/icon.svg");
});

test("failed or malformed catalogs retain no data and permit a successful retry", async () => {
    for (const failure of [response({}, "text/html"), response({}), {ok: false}]) {
        let calls = 0;
        const context = loaderContext({fetch: async () => ++calls === 1 ? failure : response(catalog())});
        const form = {dataset: {metadataUrl: "/catalog.json"}};
        await assert.rejects(context.loadQualityMetadata(form));
        assert.equal(context.qualityMetadata(form), null);
        await context.loadQualityMetadata(form);
        assert.equal(calls, 2);
        assert.ok(context.qualityMetadata(form));
    }
});

test("external catalog origins are rejected before fetching and legacy inline data is reused", async () => {
    let calls = 0;
    const context = loaderContext({fetch: async () => {calls++; return response(catalog());}});
    await assert.rejects(context.loadQualityMetadata({dataset: {metadataUrl: "https://other.example/catalog.json"}}));
    assert.equal(calls, 0);
    const form = {dataset: {metadata: JSON.stringify(catalog())}};
    const first = context.qualityMetadata(form);
    assert.equal(context.qualityMetadata(form), first);
    assert.equal(await context.loadQualityMetadata(form), first);
    assert.equal(calls, 0);
});

function detailFixture() {
    const handlers = {}, dialogs = new Map(), attempts = [];
    class StatusElement {
        constructor() {this.childNodes = []; this.attributes = new Map(); this.text = "";}
        setAttribute(name, value) {this.attributes.set(name, value);}
        getAttribute(name) {return this.attributes.get(name);}
        replaceChildren(...nodes) {this.text = ""; this.childNodes = nodes;}
        set textContent(value) {this.text = value; this.childNodes = [];}
        get textContent() {return this.text + this.childNodes.map(node => node.textContent).join("");}
    }
    class Dialog {
        constructor(id, invalid = false) {this.id = id; this.nodeName = "DIALOG"; this.dataset = {}; this.invalid = invalid; this.open = false;}
        querySelector() {return this.invalid ? {} : null;}
        querySelectorAll() {return [];}
        removeAttribute() {this.open = false;}
        showModal() {this.open = true;}
        close() {this.open = false;}
        remove() {dialogs.delete(this.id);}
        dispatchEvent() {}
    }
    const context = loaderContext({
        HTMLDialogElement: Dialog,
        document: {
            querySelector: () => null,
            querySelectorAll: () => [],
            getElementById: id => dialogs.get(id),
            addEventListener(type, handler) {(handlers[type] ||= []).push(handler);},
            createElement: () => new StatusElement(),
            body: {append(dialog) {dialogs.set(dialog.id, dialog);}},
        },
        DOMParser: class {parseFromString(value) {return {getElementById: id => value.id === id ? value : null};}},
        fetch: (url, options) => new Promise((resolve, reject) => attempts.push({url, options, resolve, reject})),
    });
    function link(id) {
        return {
            href: `https://aranami.example/quality-analysis/resources/${id}.html#${id}`,
            dataset: {qualityDetail: id}, textContent: "40 articles", isConnected: true, attributes: new Map(),
            hasAttribute(name) {return this.attributes.has(name);},
            setAttribute(name, value) {this.attributes.set(name, value);},
            removeAttribute(name) {this.attributes.delete(name);},
            after() {}, focus() {this.focused = true;},
        };
    }
    function click(link, extras = {}) {
        const event = {target: {closest: () => link}, preventDefault() {this.prevented = true;}, stopPropagation() {this.stopped = true;}, ...extras};
        return {event, complete: handlers.click[0](event)};
    }
    context.initializeQualityDetails();
    return {context, handlers, dialogs, attempts, Dialog, link, click};
}

test("detail links preserve modified clicks, target navigation and missing-dialog fallback", async () => {
    const view = detailFixture();
    for (const extras of [{ctrlKey: true}, {metaKey: true}, {shiftKey: true}, {altKey: true}, {button: 1}]) {
        const {event, complete} = view.click(view.link("author-1"), extras);
        await complete;
        assert.equal(event.prevented, undefined);
    }
    const target = view.link("author-1"); target.target = "_blank";
    assert.equal(view.click(target).event.prevented, undefined);
    view.context.HTMLDialogElement = undefined;
    assert.equal(view.click(view.link("author-1")).event.prevented, undefined);
    assert.equal(view.attempts.length, 0);
});

test("detail loading is deduplicated, opens only the latest intent and restores focus", async () => {
    const view = detailFixture();
    const first = view.link("author-1"), second = view.link("author-2");
    const click1 = view.click(first), repeated = view.click(first);
    assert.equal(view.attempts.length, 1);
    assert.equal(first.hasAttribute("aria-busy"), true);
    assert.equal(first.qualityDetailStatus.childNodes.length, 2);
    assert.equal(first.qualityDetailStatus.childNodes[0].className, "quality-detail-loading");
    const click2 = view.click(second);
    assert.equal(view.attempts.length, 2);
    assert.equal(click1.event.prevented, true);
    assert.equal(first.hasAttribute("aria-busy"), false);
    assert.equal(first.qualityDetailStatus.textContent, "");
    assert.equal(second.hasAttribute("aria-busy"), true);
    view.attempts[0].resolve(response(new view.Dialog("author-1"), "text/html"));
    await Promise.all([click1.complete, repeated.complete]);
    assert.equal(view.dialogs.size, 0);
    view.attempts[1].resolve(response(new view.Dialog("author-2"), "text/html"));
    await click2.complete;
    assert.equal(view.dialogs.get("author-2").open, true);
    assert.equal(view.dialogs.has("author-1"), false);
    assert.equal(second.hasAttribute("aria-busy"), false);
    assert.equal(second.qualityDetailStatus.childNodes.length, 0);
    second.focused = false;
    view.handlers.close[0]({target: view.dialogs.get("author-2")});
    assert.equal(second.focused, true);
    await view.click(second).complete;
    assert.equal(view.attempts.length, 2);
});

test("invalid detail HTML stays out of the document and the original link retries", async () => {
    const view = detailFixture(), link = view.link("author-1");
    const first = view.click(link);
    view.attempts[0].resolve(response(new view.Dialog("author-1", true), "text/html"));
    await first.complete;
    assert.equal(view.dialogs.size, 0);
    assert.match(link.qualityDetailStatus.textContent, /could not load/);
    assert.equal(link.qualityDetailStatus.childNodes.length, 0);
    const retry = view.click(link);
    assert.equal(link.qualityDetailStatus.childNodes[0].className, "quality-detail-loading");
    view.attempts[1].resolve(response(new view.Dialog("author-1"), "text/html"));
    await retry.complete;
    assert.equal(view.dialogs.get("author-1").open, true);
    assert.equal(link.qualityDetailStatus.textContent, "");
});

test("Escape cancels a pending detail opening without inserting its response", async () => {
    const view = detailFixture(), link = view.link("author-1");
    const pending = view.click(link);
    view.handlers.keydown[0]({key: "Escape"});
    assert.equal(link.hasAttribute("aria-busy"), false);
    assert.equal(link.qualityDetailStatus.childNodes.length, 0);
    view.attempts[0].resolve(response(new view.Dialog("author-1"), "text/html"));
    await pending.complete;
    assert.equal(view.dialogs.size, 0);
    assert.equal(link.qualityDetailStatus.textContent, "");
});

test("pending details keep the article count unchanged and show a spinner with a hidden localized announcement", async () => {
    const view = detailFixture(), link = view.link("author-1");
    link.textContent = "40条目";
    link.dataset.loading = "正在加载作者详情…";
    const pending = view.click(link);
    assert.equal(link.textContent, "40条目");
    assert.equal(link.hasAttribute("aria-busy"), true);
    const [spinner, announcement] = link.qualityDetailStatus.childNodes;
    assert.equal(link.qualityDetailStatus.getAttribute("role"), "status");
    assert.equal(spinner.className, "quality-detail-loading");
    assert.equal(spinner.getAttribute("aria-hidden"), "true");
    assert.equal(spinner.textContent, "");
    assert.equal(announcement.className, "visually-hidden");
    assert.equal(announcement.textContent, "正在加载作者详情…");
    view.attempts[0].resolve(response(new view.Dialog("author-1"), "text/html"));
    await pending.complete;
    assert.equal(link.textContent, "40条目");
    assert.equal(link.qualityDetailStatus.childNodes.length, 0);
});

test("newly loaded details inherit the current cohort before table enhancement", () => {
    const rows = [{dataset: {matrixPage: "12"}}, {dataset: {matrixPage: "13"}}];
    const context = loaderContext();
    context.document.querySelector = () => ({qualitySelectedIds: new Set(["13"])});
    context.applyQualityDetailCohort({querySelectorAll: () => rows});
    assert.equal(rows[0].dataset.cohort, "0");
    assert.equal(rows[1].dataset.cohort, "1");
});

test("details opened before catalog loading use the server-rendered initial selection", () => {
    const rows = [{dataset: {matrixPage: "12"}}, {dataset: {matrixPage: "13"}}];
    const form = {dataset: {selectedPageIds: "[12]"}};
    const context = loaderContext();
    context.document.querySelector = () => form;
    context.applyQualityDetailCohort({querySelectorAll: () => rows});
    assert.equal(rows[0].dataset.cohort, "1");
    assert.equal(rows[1].dataset.cohort, "0");
    form.qualitySelectedIds = new Set(["13"]);
    context.applyQualityDetailCohort({querySelectorAll: () => rows});
    assert.equal(rows[0].dataset.cohort, "0");
    assert.equal(rows[1].dataset.cohort, "1");
});

test("closed legacy author dialogs do not build or sort their tables at startup", () => {
    const view = detailFixture();
    const host = {closest: () => ({open: false}), querySelector() {throw new Error("Closed table was initialized");}};
    view.context.document.querySelectorAll = selector => selector === "[data-quality-authorship-table]" ? [host] : [];
    assert.doesNotThrow(() => view.context.initializeQualityAuthorship());
});

function attributionFixture() {
    class Element {
        constructor(tag) {
            this.tagName = tag; this.childNodes = []; this.text = "";
            this.dataset = {}; this.attributes = new Map(); this.handlers = {};
            this.classList = {add: name => {this.className = [this.className, name].filter(Boolean).join(" ");}};
        }
        append(...children) {this.childNodes.push(...children);}
        replaceChildren(...children) {this.text = ""; this.childNodes = children;}
        set textContent(value) {this.text = value; this.childNodes = [];}
        get textContent() {return this.text + this.childNodes.map(child => child.textContent).join("");}
        get cells() {return this.childNodes.filter(child => ["td", "th"].includes(child.tagName));}
        setAttribute(name, value) {this.attributes.set(name, value); if (name === "class") this.className = value;}
        getAttribute(name) {return this.attributes.get(name);}
        addEventListener(name, callback) {(this.handlers[name] ||= []).push(callback);}
        cloneNode() {
            const clone = new Element(this.tagName);
            clone.text = this.text; clone.className = this.className;
            clone.dataset = {...this.dataset}; clone.attributes = new Map(this.attributes);
            clone.childNodes = this.childNodes.map(child => child.cloneNode(true));
            return clone;
        }
    }
    const context = loaderContext();
    context.document.createElement = tag => new Element(tag);
    context.document.createElementNS = (_, tag) => new Element(tag);
    context.document.createTextNode = text => {const node = new Element("#text"); node.textContent = text; return node;};
    context.document.documentElement = {lang: "en"};
    const body = new Element("div");
    const labels = {authorship_unavailable: "Data not acquired", no_top_editors: "Data acquired; no editor contributed at least 10%.", main_unavailable: "Not obtained", no_main_contributors: "None", contributors: "Editor", editor_share: "Share", editor_bytes: "Bytes", until: "Through ", visualization: "Visualization"};
    return {context, body, labels, Element};
}

test("unavailable attribution does not claim a saved empty contributor result", () => {
    for (const proof of [undefined, {available: false, editors: []}]) {
        const view = attributionFixture();
        view.context.renderAuthorshipDetails(view.body, proof, view.labels);
        assert.equal(view.body.textContent, "Data not acquired");
        assert.equal(view.body.childNodes.some(node => node.tagName === "table"), false);
    }
});

test("acquired attribution with no qualifying editor has an explicit empty row without needing an endpoint", () => {
    const view = attributionFixture();
    view.context.renderAuthorshipDetails(view.body, {available: true, editors: [], range_end: null}, view.labels);
    assert.match(view.body.textContent, /Data acquired; no editor contributed at least 10%/);
    assert.doesNotMatch(view.body.textContent, /Data not acquired/);
    const table = view.body.childNodes.find(node => node.tagName === "table");
    const row = table.childNodes.find(node => node.tagName === "tbody").childNodes[0];
    assert.equal(row.childNodes[0].colSpan, 3);
});

test("saved qualifying contributors retain their exact labels and optional evidence links", () => {
    const view = attributionFixture();
    view.context.renderAuthorshipDetails(view.body, {
        available: true, range_end: null, visualization_url: "https://example.org/evidence",
        editors: [{name: "Qualifying editor", url: "https://example.org/user", share_label: "10.1%", bytes_label: "101"}],
    }, view.labels);
    assert.match(view.body.textContent, /Qualifying editor10.1%101/);
    assert.doesNotMatch(view.body.textContent, /Data not acquired|no editor contributed/);
    assert.equal(view.body.childNodes[0].childNodes[0].href, "https://example.org/evidence");
});

test("every article render creates fresh rows while preserving initial escaped markup", () => {
    const view = attributionFixture(), original = new view.Element("tr"), cell = new view.Element("td");
    original.dataset.pageId = "12";
    cell.textContent = "<escaped title>";
    cell.className = "analysis-number";
    original.append(cell);
    const first = view.context.articleElement(original), second = view.context.articleElement(original);
    assert.notEqual(first, original);
    assert.notEqual(first, second);
    assert.notEqual(first.cells[0], second.cells[0]);
    assert.equal(first.textContent, "<escaped title>");
    assert.equal(first.cells[0].className, "analysis-number");
    first.cells[0].textContent = "changed visible row";
    assert.equal(view.context.articleElement(original).textContent, "<escaped title>");
});

test("catalog rows rebuild the prepared table's chips, talk links, evidence and measurement styling", () => {
    const view = attributionFixture();
    const article = {page_id: 12, title: "Example Title", title_label: "Example Title", grade: "GA", importance: "High", listed_date: "2026-01-02", listed_oldid: 123,
        date_label: "2026-01-02", authors: ["Editor"], prose_characters: 1234, source_bytes: 12500,
        review_url: "https://zh.wikipedia.org/wiki/Talk:Example_Title#Review", authorship: {available: true}, main_contributors: [{name: "Editor", label: "Editor", url: "https://zh.wikipedia.org/wiki/User:Editor"}]};
    const metadata = {grades: [{value: "GA", article_label: "Good article", color: "#abcdef"}], importance: [{value: "High", label: "High", article_color: "#fedcba"}], authorshipIcon: "/icons.svg#notice",
        labels: {...view.labels, parenthesis_open: " (", parenthesis_close: ")", talk: "talk", review: "review", listing_evidence: "Listing revision", authorship_details: "Attribution: {title}", words: "characters"}};
    const record = {article, metadata, dataset: {pageId: "12"}, cells: Array.from({length: 8}, (_, i) => ({textContent: String(i), dataset: {sort: String(i)}}))};
    const row = view.context.articleElement(record), again = view.context.articleElement(record);
    assert.notEqual(row, again);
    assert.equal(Object.hasOwn(record, "element"), false);
    assert.equal(row.cells[1].childNodes[2].className, "analysis-talk");
    assert.match(row.cells[1].textContent, /Example Title\s+\(talk\)/);
    assert.equal(row.cells[2].childNodes[0].className, "analysis-assessment-label");
    assert.equal(row.cells[2].childNodes[0].childNodes[0].getAttribute("class"), "analysis-swatch");
    assert.equal(row.cells[2].childNodes[0].childNodes[0].childNodes[0].getAttribute("fill"), "#abcdef");
    assert.equal(row.cells[2].textContent, "Good article");
    assert.equal(row.cells[3].childNodes[0].childNodes[0].childNodes[0].getAttribute("fill"), "#fedcba");
    assert.equal(row.cells[4].childNodes[0].title, "Listing revision");
    assert.equal(row.cells[4].childNodes[0].childNodes[0].tagName, "time");
    assert.equal(row.cells[4].childNodes[0].childNodes[0].dateTime, "2026-01-02");
    assert.equal(row.cells[4].childNodes[2].className, "analysis-review");
    assert.equal(row.cells[5].childNodes.at(-1).className, "analysis-authorship-info");
    assert.equal(row.cells[5].childNodes.at(-1).handlers.click.length, 1);
    assert.equal(again.cells[5].childNodes.at(-1).handlers.click.length, 1);
    assert.equal(row.cells[6].className, "analysis-number");
    assert.equal(row.cells[6].textContent, "1,234 characters");
    assert.equal(row.cells[7].textContent, "12.5 kB");
    article.listed_date = article.date_label = article.listed_oldid = article.review_url = null;
    record.cells[4].dataset.sort = "";
    metadata.labels.unknown_listing = "未取得日期";
    const missingDate = view.context.articleElement(record).cells[4];
    assert.equal(missingDate.textContent, "未取得日期");
    assert.equal(missingDate.dataset.sort, "");
    assert.equal(missingDate.childNodes[0].className, "context");
    assert.equal(missingDate.cloneNode(true).textContent, "未取得日期");
});

test("contribution types match only explicit main or proven significant names, never arbitrary editors", () => {
    const context = loaderContext();
    const form = {dataset: {}, qualityMetadata: {
        grades: [{value: "GA", order: 4}], importance: [{value: "High", order: 1}],
        extra: [[12, "Article", "GA", "High", null, null, 50, 100, ["Main Contributor"], [{name: "Mere Editor", rank: 1}], "Article", null,
            {available: true, editors: [{name: "Significant Contributor"}]}, null,
            [{name: "Main Contributor", label: "Main Contributor", url: "/wiki/User:Main_Contributor"}]]],
    }};
    context.document.querySelector = () => form;
    const row = context.catalogRows()[0], roles = context.articleContributorRoles(row);
    assert.equal(context.contributorMatches(roles, "Main Contributor", "main", "en"), true);
    assert.equal(context.contributorMatches(roles, "Main Contributor", "significant", "en"), false);
    assert.equal(context.contributorMatches(roles, " significant contributor ", "significant", "en"), true);
    assert.equal(context.contributorMatches(roles, "Significant Contributor", "main", "en"), false);
    assert.equal(context.contributorMatches(roles, "Mere Editor", "main", "en"), false);
    assert.equal(context.contributorMatches(roles, "Mere Editor", "significant", "en"), false);
    assert.equal(context.contributorMatches(roles, "Mere Editor", "all", "en"), false);
    form.qualityMetadata.extra[0][12].available = false;
    const unavailable = context.articleContributorRoles(context.catalogRows()[0]);
    assert.equal(context.contributorMatches(unavailable, "Significant Contributor", "significant", "en"), false);
    assert.equal(context.contributorMatches(unavailable, "Main Contributor", "main", "en"), false);
});

test("main contributors render anonymous labels without IP links and distinguish none from unavailable", () => {
    const view = attributionFixture(), cell = new view.Element("td");
    view.context.renderMainContributors(cell, {
        authorship: {available: true},
        main_contributors: [
            {name: "192.0.2.1", label: "匿名用户", url: null},
            {name: "Registered", label: "Registered", url: "https://example.org/User:Registered"},
        ],
    }, view.labels);
    assert.equal(cell.textContent, "匿名用户、Registered");
    assert.equal(cell.childNodes[0].tagName, "span");
    assert.equal(cell.childNodes.filter(node => node.tagName === "a").length, 1);
    assert.doesNotMatch(cell.textContent, /192\.0\.2\.1/);
    view.context.renderMainContributors(cell, {authorship: {available: true}, main_contributors: []}, view.labels);
    assert.equal(cell.textContent, "None");
    view.context.renderMainContributors(cell, {authorship: {available: false}, main_contributors: []}, view.labels);
    assert.equal(cell.textContent, "Not obtained");
});

test("qualified anonymous main labels remain searchable while exact ten and thirty-three belong only to significant", () => {
    const context = loaderContext();
    const form = {dataset: {}, qualityMetadata: {
        grades: [], importance: [], extra: [[1, "Article", "GA", "High", null, null, 1, 2,
            ["192.0.2.1"], null, "Article", null,
            {available: true, editors: [{name: "192.0.2.1", share_label: "40.0%"}, {name: "Exactly ten", share_label: "10.0%"}, {name: "Exactly thirty-three", share_label: "33.0%"}]},
            null, [{name: "192.0.2.1", label: "匿名用户", url: null}]]],
    }};
    context.document.querySelector = () => form;
    const roles = context.articleContributorRoles(context.catalogRows()[0]);
    assert.equal(roles.mainLabels[0], "匿名用户");
    assert.equal(context.contributorMatches(roles, "匿名用户", "main", "zh-Hans"), true);
    assert.equal(context.contributorMatches(roles, "192.0.2.1", "main", "zh-Hans"), true);
    for (const name of ["Exactly ten", "Exactly thirty-three"]) {
        assert.equal(context.contributorMatches(roles, name, "main", "en"), false);
        assert.equal(context.contributorMatches(roles, name, "significant", "en"), true);
    }
});

test("author text and contribution type are captured for fragment refresh", () => {
    const context = loaderContext();
    const controls = {"author": "Named contributor", "author-role": "significant", "size": "50"};
    context.document.querySelector = () => ({
        dataset: {}, querySelector: selector => ({value: controls[selector.match(/data-analysis-(.+)\]/)[1]]}),
    });
    const state = context.analysisState();
    assert.equal(state.author, "Named contributor");
    assert.equal(state.role, "significant");
    assert.equal(state.size, "50");
});

test("year charts retain selected empty columns but show only plotted grade colors in the legend", () => {
    const grades = [{value: "GA", label: "Good", color: "#111111"}, {value: "B", label: "B", color: "#222222"}];
    const cells = [{year: 2025, grade: "GA", pages: 3}, {year: null, grade: "B", pages: 2}, {year: 2024, grade: "B", pages: 4}];
    const summary = context.qualityYearSummary(cells, grades, 2025, 2026);
    assert.deepEqual(JSON.parse(JSON.stringify(summary.years)), [
        {year: 2025, pages: 3, grades: [3, 0]}, {year: 2026, pages: 0, grades: [0, 0]},
    ]);
    assert.equal(summary.legend.length, 1);
    assert.equal(summary.legend[0], grades[0]);
    assert.equal(summary.legend[0].color, "#111111");
    const filtered = context.qualityYearSummary([{year: 2024, grade: "B", pages: 4}], [grades[1]], 2024, 2024);
    assert.equal(filtered.years[0].grades.length, 1);
    assert.equal(filtered.legend[0], grades[1]);
    assert.equal(context.qualityYearSummary([], [], 2025, 2026).legend.length, 0);
});

test("violin quartiles use exact counted lengths and preserve the existing percentile intervals", () => {
    const result = context.combineCells([{pages: 5, measured: 4,
        histogram: [{value: 0, count: 1}, {value: 1, count: 1}, {value: 2, count: 1}, {value: 3, count: 1}], density: []}]);
    assert.equal(result.quartiles[0], .75);
    assert.equal(result.quantiles[2], 1.5);
    assert.equal(result.quartiles[1], 2.25);
    assert.equal(result.quantiles.length, 5);
    assert.equal(result.pages, 5);
    assert.equal(result.measured, 4);
    assert.equal(result.minimum, 0);
    assert.equal(result.maximum, 3);
    assert.equal(context.violinSummary(result, "Total {pages}; measured {measured}; minimum {minimum}; P25 {p25}; P50 {p50}; P75 {p75}; maximum {maximum}"),
        "Total 5; measured 4; minimum 0; P25 1; P50 2; P75 2; maximum 3");
});

test("violin missing measurements remain unknown while zero and constant quartiles stay observed", () => {
    const missing = context.combineCells([{pages: 2, measured: 0, histogram: [], density: []}]);
    assert.equal(missing.quartiles[0], null);
    assert.equal(missing.quartiles[1], null);
    assert.equal(missing.minimum, null);
    assert.equal(missing.maximum, null);
    assert.equal(context.violinSummary(missing, "{pages}/{measured} minimum {minimum} P25 {p25} P50 {p50} P75 {p75} maximum {maximum}"), "2/0 minimum — P25 — P50 — P75 — maximum —");
    for (const value of [0, 1200]) {
        const constant = context.combineCells([{pages: 3, measured: 3, histogram: [{value, count: 3}], density: []}]);
        assert.equal(constant.quartiles[0], value);
        assert.equal(constant.quantiles[2], value);
        assert.equal(constant.quartiles[1], value);
        assert.equal(constant.minimum, value);
        assert.equal(constant.maximum, value);
        assert.doesNotMatch(context.violinSummary(constant, "minimum {minimum} P25 {p25} P50 {p50} P75 {p75} maximum {maximum}"), /—/);
    }
});

test("chart sorting maps its prose column to article measurements and leaves missing values last", () => {
    const small = row(6, "9"), large = row(6, "100"), missing = row(6, "");
    const original = [large, missing, small];
    const ascending = context.orderChartArticles(original, 5, false, "en");
    assert.equal(ascending[0], small);
    assert.equal(ascending[1], large);
    assert.equal(ascending[2], missing);
    const descending = context.orderChartArticles(original, 5, true, "en");
    assert.equal(descending[0], large);
    assert.equal(descending[1], small);
    assert.equal(descending[2], missing);
    assert.equal(original[0], large);
    assert.equal(original[1], missing);
});

test("chart sorting handles title, grade, importance and default dates with stable ties", () => {
    for (const [column, first, second] of [[1, "Alpha", "Beta"], [2, "2", "10"], [3, "1", "4"], [4, "2025-01-01", "2026-01-01"]]) {
        const left = row(column, first), right = row(column, second), unknown = row(column, "");
        const ascending = context.orderChartArticles([right, unknown, left], column, false, "en");
        assert.equal(ascending[0], left);
        assert.equal(ascending[2], unknown);
        const descending = context.orderChartArticles([left, unknown, right], column, true, "en");
        assert.equal(descending[0], right);
        assert.equal(descending[2], unknown);
    }
    const same1 = row(4, "2026-01-01"), same2 = row(4, "2026-01-01");
    const tied = context.orderChartArticles([same1, same2], 4, true, "en");
    assert.equal(tied[0], same1);
    assert.equal(tied[1], same2);
});

function chartPaginationFixture(locale, total) {
    const {context, Element} = attributionFixture();
    const body = new Element("tbody"), count = new Element("p"), heading = new Element("h3");
    const status = new Element("span"), size = new Element("select"), pager = new Element("nav");
    const dialog = new Element("dialog"), headings = Array.from({length: 6}, () => new Element("th"));
    const controls = Object.fromEntries(["first", "prev", "next", "last"].map(name => [name, new Element("button")]));
    const listeners = {};
    size.value = "20";
    pager.dataset.pageStatus = interfaceMessages["reports.page_status"][locale];
    count.dataset.label = interfaceMessages["quality_analysis.chart_article_count"][locale];
    pager.querySelector = selector => selector === "[data-chart-size]" ? size :
        selector === "[data-chart-page-status]" ? status : controls[selector.match(/data-chart-(.+)\]/)[1]];
    dialog.querySelector = selector => ({"[data-chart-article-rows]": body, "[data-chart-article-count]": count, "[data-chart-pager]": pager})[selector];
    dialog.querySelectorAll = () => headings;
    dialog.isConnected = true;
    dialog.showModal = () => {dialog.open = true;};
    context.document.documentElement.lang = locale;
    context.document.getElementById = id => id === "analysis-chart-articles" ? dialog : heading;
    context.document.querySelector = () => ({dataset: {sortCaption: "Sort by {caption}"}});
    context.document.addEventListener = (name, handler) => {(listeners[name] ||= []).push(handler);};
    const rows = Array.from({length: total}, (_, index) => {
        const record = new Element("tr");
        record.dataset = {title: `Article ${index + 1}`, grade: "GA", cohort: "1", year: "2026"};
        for (const value of [index + 1, `Article ${index + 1}`, 4, 1, `2026-01-${String(index + 1).padStart(2, "0")}`, "Contributor", index + 1, 100]) {
            const cell = new Element("td");
            cell.dataset.sort = String(value);
            cell.textContent = String(value);
            record.append(cell);
        }
        return record;
    });
    context.initializeChartArticles(rows);
    function open(grade = "GA") {
        const link = {dataset: {chartGroup: "grade", chartValue: grade, chartLabel: grade}, focus() {}};
        listeners.click[0]({target: {closest: () => link}, preventDefault() {}, stopPropagation() {}});
    }
    return {body, status, count, size, headings, controls, open};
}

test("four sector articles use each actual translated item-range template after size and sort changes", () => {
    const expected = {en: "Showing results 1–4 of 4", "zh-hans": "第1–4项，共4项", "zh-hant": "第1–4項，共4項"};
    for (const [locale, label] of Object.entries(expected)) {
        const view = chartPaginationFixture(locale, 4);
        view.open();
        assert.equal(view.body.childNodes.length, 4);
        assert.equal(view.status.textContent, label);
        assert.equal(view.controls.next.disabled, true);
        assert.doesNotMatch(view.status.textContent, /\{[^}]+\}/);
        view.size.value = "50";
        view.size.handlers.change[0]();
        assert.equal(view.status.textContent, label);
        view.headings[5].childNodes[0].handlers.click[0]();
        assert.equal(view.status.textContent, label);
        assert.equal(view.body.childNodes[0].cells[5].dataset.sort, "1");
        view.headings[5].childNodes[0].handlers.click[0]();
        assert.equal(view.status.textContent, label);
        assert.equal(view.body.childNodes[0].cells[5].dataset.sort, "4");
        view.open("B");
        assert.equal(view.body.childNodes.length, 0);
        const empty = {en: "Showing results 0–0 of 0", "zh-hans": "第0–0项，共0项", "zh-hant": "第0–0項，共0項"};
        assert.equal(view.status.textContent, empty[locale]);
    }
});

test("sector pagination reports the tail article range and sorting resets to its first item", () => {
    const view = chartPaginationFixture("zh-hans", 24);
    view.open();
    assert.equal(view.status.textContent, "第1–20项，共24项");
    view.controls.next.handlers.click[0]();
    assert.equal(view.status.textContent, "第21–24项，共24项");
    assert.equal(view.body.childNodes.length, 4);
    assert.equal(view.body.childNodes[0].cells[0].textContent, "21");
    view.headings[5].childNodes[0].handlers.click[0]();
    assert.equal(view.status.textContent, "第1–20项，共24项");
    assert.equal(view.body.childNodes[0].cells[0].textContent, "1");
    assert.equal(view.body.childNodes[0].cells[5].dataset.sort, "1");
    view.size.value = "50";
    view.size.handlers.change[0]();
    assert.equal(view.status.textContent, "第1–24项，共24项");
    assert.equal(view.controls.next.disabled, true);
    view.open();
    assert.equal(view.headings[4].getAttribute("aria-sort"), "descending");
    assert.equal(view.status.textContent, "第1–24项，共24项");
});

test("actual localized violin templates interpolate every count and digit-containing percentile token", () => {
    for (const locale of ["en", "zh-hans", "zh-hant"]) {
        const template = interfaceMessages["quality_analysis.violin_summary"][locale];
        const summary = context.violinSummary({pages: 4, measured: 3, minimum: 0, maximum: 12345, quartiles: [.25, 2.75], quantiles: [null, null, 1.5]}, template);
        assert.doesNotMatch(summary, /\{[^}]+\}/);
        assert.equal(summary, template.replace("{pages}", "4").replace("{measured}", "3")
            .replace("{minimum}", "0").replace("{p25}", "0").replace("{p50}", "2").replace("{p75}", "3").replace("{maximum}", "12,000"));
        assert.match(summary, /12,000/);
        const repeated = context.violinSummary({pages: 0, measured: 0, minimum: null, maximum: null, quartiles: [null, null], quantiles: [null, null, null]}, `${template} {p50}/{p50}`);
        assert.doesNotMatch(repeated, /\{[^}]+\}/);
        assert.ok(repeated.endsWith("—/—"));
    }
});

test("violin extrema use observed lengths rather than density bounds or central percentiles", () => {
    const result = context.combineCells([
        {pages: 3, measured: 3, histogram: [{value: 0, count: 1}, {value: 1000, count: 2}], density: [{position: -100, density: .1}, {position: 15000, density: .1}]},
        {pages: 2, measured: 2, histogram: [{value: 1000, count: 1}, {value: 12345, count: 1}, {value: 99999, count: 0}], density: []},
    ]);
    assert.equal(result.minimum, 0);
    assert.equal(result.maximum, 12345);
    assert.ok(result.quantiles[0] > result.minimum);
    assert.ok(result.quantiles[4] < result.maximum);
    assert.equal(context.violinSummary(result, "{minimum}/{p25}/{p50}/{p75}/{maximum}"), "0/1,000/1,000/1,000/12,000");
});

test("five-number tooltip rounds half lengths upward without changing the saved marker values", () => {
    const statistics = {pages: 4, measured: 4, minimum: 0, maximum: 1234.5,
        quartiles: [.5, 2.5], quantiles: [null, null, 1.5]};
    assert.equal(context.violinSummary(statistics, "{minimum}/{p25}/{p50}/{p75}/{maximum}"), "0/1/2/3/1,200");
    assert.deepEqual(statistics.quartiles, [.5, 2.5]);
    assert.equal(statistics.quantiles[2], 1.5);
    assert.equal(statistics.maximum, 1234.5);
});

test("tooltip lengths use two significant digits with integer display while article counts stay exact", () => {
    const statistics = {pages: 15412, measured: 654, minimum: .5, maximum: 15412,
        quartiles: [75.5, 654], quantiles: [null, null, 995]};
    assert.equal(context.violinSummary(statistics, "{pages}/{measured}: {minimum}/{p25}/{p50}/{p75}/{maximum}"),
        "15,412/654: 1/76/1,000/650/15,000");
    assert.deepEqual(statistics.quartiles, [75.5, 654]);
    assert.equal(statistics.maximum, 15412);
    assert.equal(statistics.quantiles[2], 995);
});
