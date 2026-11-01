"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.resolve(__dirname, "../../src/webservice/public_html/newsletter.js"), "utf8");

function fixture(clipboard) {
    const status = {textContent: ""};
    const display = {
        focused: false,
        focus() { this.focused = true; },
    };
    const source = {textContent: "{{Original}}", parentElement: display};
    const selection = {
        source: null,
        removeAllRanges() { this.source = null; },
        addRange(range) { this.source = range.source; },
    };
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
    vm.runInNewContext(script, {
        document: {
            querySelector: () => null,
            querySelectorAll: selector => selector === "[data-newsletter-copy]" ? [button] : [],
            getElementById: () => source,
            createRange: () => ({selectNodeContents(value) { this.source = value; }}),
            getSelection: () => selection,
        },
        navigator: {clipboard},
        location: {assign(url) { navigated.push(url); }},
    });
    return {button, source, display, selection, status, navigated};
}

test("copy preserves literal displayed wikitext and announces completion", async () => {
    let copied;
    let writes = 0;
    const {button, source, display, status, navigated} = fixture({writeText: async value => { copied = value; writes++; }});
    assert.equal(button.hidden, false);
    source.textContent = "{{模板|中文}}\n[[Article]]";
    await button.click();
    assert.equal(copied, source.textContent);
    assert.equal(writes, 1);
    assert.equal(status.textContent, "Copied");
    assert.equal(button.disabled, false);
    assert.equal(display.focused, false);
    assert.deepEqual(navigated, []);
});

test("missing or rejected clipboard selects source text for manual copying", async () => {
    for (const clipboard of [undefined, {writeText: async () => { throw new Error("Denied"); }}]) {
        const {button, source, display, selection, status, navigated} = fixture(clipboard);
        await button.click();
        assert.equal(display.focused, true);
        assert.equal(selection.source, source);
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
    view.source.textContent = "{{模板|中文}}\n[[Target]]\n<source>";
    const complete = view.button.click();
    assert.equal(copied, view.source.textContent);
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
    const display = {textContent: text};
    const created = [];
    vm.runInNewContext(script, {
        document: {
            querySelector: () => null,
            querySelectorAll: selector => selector === "[data-newsletter-wikitext]" ? [display] : [],
            createElement(tag) { created.push(tag); return {}; },
        },
        navigator: {},
    });
    assert.equal(display.textContent, text);
    assert.deepEqual(created, []);
});

function issueFixture() {
    const issue = {value: "2027-01", handlers: {}, addEventListener(name, handler) { this.handlers[name] = handler; }};
    const submit = {hidden: false};
    const statusText = {textContent: ""};
    const status = {
        hidden: true, dataset: {failure: "Unable to load this issue"},
        querySelector: () => statusText,
        get textContent() { return statusText.textContent; },
    };
    const requests = [];
    const addresses = [];
    let current;
    function result(text) {
        return {
            textContent: text, attrs: {},
            querySelector: () => null, querySelectorAll: () => [],
            setAttribute(name, value) { this.attrs[name] = value; },
            removeAttribute(name) { delete this.attrs[name]; },
            replaceWith(replacement) { current = replacement; },
        };
    }
    current = result("Original draft");
    const form = {
        action: "/newsletter/", handlers: {},
        querySelector: selector => selector === '[name="issue"]' ? issue : submit,
        addEventListener(name, handler) { this.handlers[name] = handler; },
    };
    const document = {
        querySelector: selector => ({
            "[data-newsletter-issue-form]": form,
            "[data-newsletter-load-status]": status,
            "[data-newsletter-result]": current,
        })[selector] || null,
        querySelectorAll: () => [],
    };
    class ResponseParser {
        parseFromString(text) {
            const values = JSON.parse(text);
            return {querySelector(selector) {
                if (selector === "[data-newsletter-result]") return values.missing ? null : result(values.text);
                if (selector === "#newsletter-issue") return {value: values.issue};
                return null;
            }};
        }
    }
    vm.runInNewContext(script, {
        document, URL, URLSearchParams, AbortController,
        DOMParser: ResponseParser,
        FormData: class { *[Symbol.iterator]() { yield ["issue", issue.value]; yield ["lang", "zh-hant"]; } },
        location: {href: "https://example.test/newsletter/?issue=2027-01&date=2026-12-31", origin: "https://example.test"},
        history: {replaceState(_state, _title, url) { addresses.push(String(url)); }},
        fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})),
    });
    function respond(index, values, ok = true) {
        requests[index].resolve({ok, text: async () => JSON.stringify(values)});
    }
    return {issue, submit, status, requests, addresses, form, respond, result: () => current};
}

test("issue selection reads the new issue and replaces its draft without submission", async () => {
    const view = issueFixture();
    assert.equal(view.submit.hidden, false);
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
    assert.equal(view.submit.hidden, false);
    assert.equal(view.status.hidden, true);
    assert.equal(view.addresses.length, 1);
});

test("failed or mismatched issue loads retain the displayed draft and explain recovery", async () => {
    for (const failure of ["network", "http", "wrong-issue", "missing-result"]) {
        const view = issueFixture();
        view.issue.value = "2026-10";
        const loaded = view.issue.handlers.change();
        if (failure === "network") view.requests[0].reject(new Error("Offline"));
        else view.respond(0, {issue: failure === "wrong-issue" ? "2027-04" : "2026-10", text: "Wrong draft", missing: failure === "missing-result"}, failure !== "http");
        await loaded;
        assert.equal(view.result().textContent, "Original draft", failure);
        assert.equal(view.issue.value, "2027-01", failure);
        assert.equal(view.submit.hidden, false, failure);
        assert.equal(view.result().attrs["aria-busy"], undefined, failure);
        assert.equal(view.status.textContent, "Unable to load this issue", failure);
        assert.equal(view.status.hidden, false, failure);
        assert.equal(view.addresses.length, 0, failure);
    }
});

test("late issue responses cannot replace a more recent selection", async () => {
    const view = issueFixture();
    view.issue.value = "2026-10";
    const first = view.issue.handlers.change();
    view.issue.value = "2027-04";
    const latest = view.issue.handlers.change();
    assert.equal(view.requests[0].options.signal.aborted, true);
    assert.equal(view.requests[1].options.signal.aborted, false);
    view.respond(1, {issue: "2027-04", text: "Latest requested draft"});
    await latest;
    view.respond(0, {issue: "2026-10", text: "Obsolete draft"});
    await first;
    assert.equal(view.result().textContent, "Latest requested draft");
    assert.equal(view.issue.value, "2027-04");
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
