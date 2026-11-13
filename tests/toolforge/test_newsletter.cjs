"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.resolve(__dirname, "../../src/webservice/public_html/newsletter.js"), "utf8");

function fixture(clipboard) {
    const status = {textContent: ""};
    const accordion = {open: false, handlers: {}, addEventListener(type, handler) { this.handlers[type] = handler; }};
    const display = {
        value: "{{Original}}",
        textContent: "Unrelated initial text",
        focused: false,
        selected: false,
        rows: 8,
        scrollHeight: 432,
        closest: () => accordion,
        focus() { this.focused = true; },
        select() { this.selected = true; },
    };
    const source = display;
    const button = {
        hidden: false,
        disabled: false,
        dataset: {
            newsletterCopy: "draft",
            copySuccess: "Copied",
            copyFailure: "Select and copy manually",
        },
        parentElement: {querySelector: () => status},
        addEventListener(_type, handler) { this.click = handler; },
    };
    const navigated = [];
    const handlers = {};
    vm.runInNewContext(script, {
        document: {
            querySelector: () => null,
            querySelectorAll: selector => selector === "[data-newsletter-copy]" ? [button] : selector === "[data-newsletter-wikitext]" ? [source] : [],
            getElementById: () => source,
        },
        getComputedStyle: () => ({lineHeight: "20px", paddingTop: "16px", paddingBottom: "16px"}),
        addEventListener(type, handler) { handlers[type] = handler; },
        navigator: {clipboard},
        location: {assign(url) { navigated.push(url); }},
    });
    return {button, source, display, accordion, handlers, status, navigated};
}

test("copy preserves literal displayed wikitext and announces completion", async () => {
    let copied;
    let writes = 0;
    const {button, source, display, status, navigated} = fixture({writeText: async value => { copied = value; writes++; }});
    assert.equal(button.hidden, false);
    source.value = "\n{{模板|中文}}\n[[Article]]";
    await button.click();
    assert.equal(copied, source.value);
    assert.equal(writes, 1);
    assert.equal(status.textContent, "Copied");
    assert.equal(button.disabled, false);
    assert.equal(display.focused, false);
    assert.deepEqual(navigated, []);
});

test("missing or rejected clipboard selects source text for manual copying", async () => {
    for (const clipboard of [undefined, {writeText: async () => { throw new Error("Denied"); }}]) {
        const {button, source, display, accordion, status, navigated} = fixture(clipboard);
        await button.click();
        assert.equal(display.focused, true);
        assert.equal(accordion.open, true);
        assert.equal(source.selected, true);
        assert.equal(source.rows, 20);
        assert.equal(status.textContent, "Select and copy manually");
        assert.equal(button.disabled, false);
        assert.deepEqual(navigated, []);
    }
});

test("copy waits for its clipboard write without navigating", async () => {
    let finishCopy;
    let copied;
    const view = fixture({writeText: value => {
        copied = value;
        return new Promise(resolve => { finishCopy = resolve; });
    }});
    view.source.value = "{{模板|中文}}\n[[Target]]\n<source>";
    const complete = view.button.click();
    assert.equal(copied, view.source.value);
    assert.equal(view.button.disabled, true);
    assert.deepEqual(view.navigated, []);
    finishCopy();
    await complete;
    assert.deepEqual(view.navigated, []);
    assert.equal(view.status.textContent, "Copied");
    assert.equal(view.button.disabled, false);
});

test("draft text remains literal without highlighting or HTML interpretation", () => {
    const text = "== 中文 ==\n{{模板|foo=[[條目|label]]}}\n<!-- {{hidden}} -->\n</code><script>alert(1)</script>\n''italic''\n";
    const display = {value: text, closest: () => null};
    const created = [];
    vm.runInNewContext(script, {
        document: {
            querySelector: () => null,
            querySelectorAll: selector => selector === "[data-newsletter-wikitext]" ? [display] : [],
            createElement(tag) { created.push(tag); return {}; },
        },
        navigator: {},
        addEventListener() {},
    });
    assert.equal(display.value, text);
    assert.deepEqual(created, []);
});

test("fallback sizing measures wrapped text on expansion and viewport changes", () => {
    const view = fixture({writeText: async () => {}});
    assert.equal(view.source.rows, 8, "closed drafts retain the native fallback");
    view.accordion.open = true;
    view.accordion.handlers.toggle();
    assert.equal(view.source.rows, 20);
    view.source.scrollHeight = 232;
    view.handlers.resize();
    assert.equal(view.source.rows, 10, "wider layouts can shrink the text area");
    view.accordion.open = false;
    view.source.scrollHeight = 0;
    view.accordion.handlers.toggle();
    assert.equal(view.source.rows, 10, "hidden content must not replace measured rows");
});

function issueFixture({fetchAvailable = true} = {}) {
    function selection(value, choices = ["2026-10", "2027-01", "2027-04"].map(value => ({value}))) {
        return {
            value,
            options: choices.map(choice => ({disabled: false, hidden: false, textContent: choice.value, ...choice})),
            get selectedOptions() { return this.options.filter(option => option.value === this.value); },
            replaceChildren(...options) { this.options = options; },
            handlers: {},
            addEventListener(name, handler) { this.handlers[name] = handler; },
        };
    }
    const issue = selection("2027-01");
    const statusText = {textContent: ""};
    const status = {
        hidden: true, dataset: {failure: "Unable to load this issue"},
        querySelector: () => statusText,
        get textContent() { return statusText.textContent; },
    };
    const requests = [];
    const submissions = [];
    const addresses = [];
    let current;
    function result(text) {
        const date = {
            value: "2026-12-31", handlers: {},
            form: {requestSubmit() { submissions.push({issue: issue.value, date: date.value}); }},
            addEventListener(name, handler) { this.handlers[name] = handler; },
        };
        return {
            textContent: text, attrs: {}, date,
            querySelector: () => null,
            querySelectorAll: selector => selector === '#newsletter-data-date' ? [date] : [],
            setAttribute(name, value) { this.attrs[name] = value; },
            removeAttribute(name) { delete this.attrs[name]; },
            replaceWith(replacement) { current = replacement; },
        };
    }
    current = result("Original draft");
    const form = {
        action: "/newsletter/", handlers: {},
        querySelector: selector => selector === '[name="issue"]' ? issue : null,
        requestSubmit() { submissions.push({issue: issue.value}); },
        addEventListener(name, handler) { this.handlers[name] = handler; },
    };
    const document = {
        querySelector: selector => ({
            "[data-newsletter-issue-form]": form,
            "[data-newsletter-load-status]": status,
            "[data-newsletter-result]": current,
        })[selector] || null,
        querySelectorAll: selector => selector === '#newsletter-data-date' ? [current.date] : [],
    };
    class ResponseParser {
        parseFromString(text) {
            const values = JSON.parse(text);
            return {querySelector(selector) {
                if (selector === "[data-newsletter-result]") return values.missing ? null : result(values.text);
                if (selector === "#newsletter-issue") return selection(values.issue, values.choices);
                return null;
            }};
        }
    }
    vm.runInNewContext(script, {
        document, URL, URLSearchParams, AbortController,
        addEventListener() {},
        DOMParser: ResponseParser,
        FormData: class { *[Symbol.iterator]() { yield ["issue", issue.value]; yield ["lang", "zh-hant"]; } },
        location: {href: "https://example.test/newsletter/?issue=2027-01&date=2026-12-31", origin: "https://example.test"},
        history: {replaceState(_state, _title, url) { addresses.push(String(url)); }},
        fetch: fetchAvailable ? (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})) : undefined,
    });
    function respond(index, values, ok = true) {
        requests[index].resolve({ok, text: async () => JSON.stringify(values)});
    }
    return {issue, status, requests, submissions, addresses, form, respond, result: () => current};
}

test("issue selection reads the new issue and replaces its draft without submission", async () => {
    const view = issueFixture();
    view.issue.value = "2026-10";
    const loaded = view.issue.handlers.change();
    assert.equal(view.requests.length, 1);
    assert.equal(view.requests[0].url.pathname, "/newsletter/");
    assert.equal(view.requests[0].url.searchParams.get("issue"), "2026-10");
    assert.equal(view.requests[0].url.searchParams.get("lang"), "zh-hant");
    assert.equal(view.requests[0].url.searchParams.has("date"), false);
    assert.equal(view.requests[0].options.redirect, "error");
    assert.equal(view.result().attrs["aria-busy"], "true");
    view.respond(0, {issue: "2026-10", text: "Previous issue draft"});
    await loaded;
    assert.equal(view.result().textContent, "Previous issue draft");
    assert.equal(view.result().attrs["aria-busy"], undefined);
    assert.equal(view.issue.value, "2026-10");
    assert.equal(view.status.hidden, true);
    assert.equal(view.addresses.length, 1);
});

test("failed or mismatched issue loads retain the displayed draft and explain recovery", async () => {
    for (const failure of ["network", "http", "wrong-issue", "missing-result"]) {
        const view = issueFixture();
        const choices = [...view.issue.options];
        view.issue.value = "2026-10";
        const loaded = view.issue.handlers.change();
        if (failure === "network") view.requests[0].reject(new Error("Offline"));
        else view.respond(0, {issue: failure === "wrong-issue" ? "2027-04" : "2026-10", text: "Wrong draft", missing: failure === "missing-result"}, failure !== "http");
        await loaded;
        assert.equal(view.result().textContent, "Original draft", failure);
        assert.equal(view.issue.value, "2027-01", failure);
        assert.deepEqual(view.issue.options, choices, failure);
        assert.equal(view.result().attrs["aria-busy"], undefined, failure);
        assert.equal(view.status.textContent, "Unable to load this issue", failure);
        assert.equal(view.status.hidden, false, failure);
        assert.equal(view.addresses.length, 0, failure);
    }
});

test("successful issue loads refresh available choices without replacing the issue control", async () => {
    const view = issueFixture();
    const control = view.issue;
    const change = control.handlers.change;
    view.issue.value = "2027-04";
    const loaded = change();
    view.respond(0, {
        issue: "2027-04", text: "Upcoming draft",
        choices: [
            {value: "2027-04", textContent: "2027-04 (Scheduled)"},
            {value: "2026-10", textContent: "2026-10 (Unpublished)"},
        ],
    });
    await loaded;
    assert.equal(view.issue, control);
    assert.equal(view.issue.handlers.change, change);
    assert.deepEqual(view.issue.options.map(option => option.value), ["2027-04", "2026-10"]);
    assert.equal(view.issue.options[0].textContent, "2027-04 (Scheduled)");
    assert.equal(view.issue.value, "2027-04");
    assert.equal(view.result().textContent, "Upcoming draft");
});

test("an issue archived during loading remains readable but cannot be resubmitted", async () => {
    const view = issueFixture();
    view.issue.value = "2026-10";
    const loaded = view.issue.handlers.change();
    view.respond(0, {
        issue: "2026-10", text: "Archived draft",
        choices: [
            {value: "2026-10", textContent: "2026-10 (Archived issue)", disabled: true, hidden: true},
            {value: "2027-01"},
        ],
    });
    await loaded;
    assert.equal(view.issue.value, "2026-10");
    assert.equal(view.issue.selectedOptions[0].disabled, true);
    assert.equal(view.issue.selectedOptions[0].hidden, true);
    assert.equal(view.result().textContent, "Archived draft");
    assert.equal(view.status.hidden, true);
    assert.equal(view.addresses.length, 1);
    let prevented = false;
    view.form.handlers.submit({preventDefault() { prevented = true; }});
    await view.issue.handlers.change();
    assert.equal(prevented, true);
    assert.equal(view.requests.length, 1, "disabled archive choices must not produce requests");
    view.issue.value = "2027-01";
    const next = view.issue.handlers.change();
    assert.equal(view.requests.length, 2, "available issues remain selectable from an archive");
    view.respond(1, {issue: "2027-01", text: "Upcoming draft"});
    await next;
    assert.equal(view.result().textContent, "Upcoming draft");
});

test("late issue responses cannot replace a more recent selection", async () => {
    const view = issueFixture();
    view.issue.value = "2026-10";
    const first = view.issue.handlers.change();
    view.issue.value = "2027-04";
    const latest = view.issue.handlers.change();
    assert.equal(view.requests[0].options.signal.aborted, true);
    assert.equal(view.requests[1].options.signal.aborted, false);
    view.respond(1, {issue: "2027-04", text: "Latest requested draft", choices: [{value: "2027-04"}]});
    await latest;
    view.respond(0, {issue: "2026-10", text: "Obsolete draft"});
    await first;
    assert.equal(view.result().textContent, "Latest requested draft");
    assert.equal(view.issue.value, "2027-04");
    assert.deepEqual(view.issue.options.map(option => option.value), ["2027-04"]);
    assert.equal(view.addresses.length, 1);
    assert.equal(view.status.hidden, true);
});

test("late failed requests cannot reset a successful current issue", async () => {
    const view = issueFixture();
    view.issue.value = "2026-10";
    const first = view.issue.handlers.change();
    view.issue.value = "2027-04";
    const latest = view.issue.handlers.change();
    view.respond(1, {issue: "2027-04", text: "Latest requested draft"});
    await latest;
    view.requests[0].reject(new Error("Late abort"));
    await first;
    assert.equal(view.result().textContent, "Latest requested draft");
    assert.equal(view.issue.value, "2027-04");
    assert.equal(view.status.hidden, true);
});

test("date selection submits the native form initially and after an issue replacement", async () => {
    const view = issueFixture();
    const originalDate = view.result().date;
    originalDate.value = "";
    originalDate.handlers.change();
    assert.deepEqual(view.submissions, [], "clearing the date must not navigate");
    originalDate.value = "2026-12-30";
    originalDate.handlers.change();
    assert.deepEqual(view.submissions, [{issue: "2027-01", date: "2026-12-30"}]);
    view.issue.value = "2026-10";
    const loaded = view.issue.handlers.change();
    view.respond(0, {issue: "2026-10", text: "Previous issue draft"});
    await loaded;
    const date = view.result().date;
    assert.notEqual(date, originalDate);
    date.value = "2026-09-30";
    date.handlers.change();
    assert.deepEqual(view.submissions.at(-1), {issue: "2026-10", date: "2026-09-30"});
});

test("issue changes submit natively when in-place loading is unavailable", () => {
    const view = issueFixture({fetchAvailable: false});
    view.issue.value = "2026-10";
    view.issue.handlers.change();
    assert.deepEqual(view.submissions, [{issue: "2026-10"}]);
    assert.deepEqual(view.requests, []);
});
