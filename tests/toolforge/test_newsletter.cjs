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
        hidden: true,
        disabled: false,
        dataset: {
            newsletterCopy: "draft",
            copySuccess: "Copied",
            copyFailure: "Select and copy manually",
        },
        parentElement: {querySelector: () => status},
        addEventListener(_type, handler) { this.click = handler; },
    };
    vm.runInNewContext(script, {
        document: {
            querySelector: () => null,
            querySelectorAll: selector => selector === "[data-newsletter-copy]" ? [button] : [],
            getElementById: () => source,
            createRange: () => ({selectNodeContents(value) { this.source = value; }}),
            getSelection: () => selection,
        },
        navigator: {clipboard},
    });
    return {button, source, display, selection, status};
}

test("copy preserves literal displayed wikitext and announces completion", async () => {
    let copied;
    const {button, source, display, status} = fixture({writeText: async value => { copied = value; }});
    assert.equal(button.hidden, false);
    source.textContent = "{{模板|中文}}\n[[Article]]";
    await button.click();
    assert.equal(copied, source.textContent);
    assert.equal(status.textContent, "Copied");
    assert.equal(button.disabled, false);
    assert.equal(display.focused, false);
});

test("issue selection submits the saved-issue GET form only on checked change", () => {
    const radios = [true, false].map(checked => ({
        checked,
        addEventListener(event, handler) {
            assert.equal(event, "change");
            this.change = handler;
        },
    }));
    const form = {
        hidden: true,
        submissions: 0,
        querySelectorAll(selector) {
            assert.equal(selector, 'input[name="issue"]');
            return radios;
        },
        requestSubmit() { this.submissions++; },
    };
    vm.runInNewContext(script, {
        document: {
            querySelector(selector) {
                assert.equal(selector, "[data-newsletter-issue-form]");
                return form;
            },
            querySelectorAll: () => [],
        },
    });
    assert.equal(form.hidden, false);
    assert.equal(form.submissions, 0);
    radios[0].checked = false;
    radios[0].change();
    assert.equal(form.submissions, 0);
    radios[1].checked = true;
    radios[1].change();
    assert.equal(form.submissions, 1);
});

test("missing or rejected clipboard selects source text for manual copying", async () => {
    for (const clipboard of [undefined, {writeText: async () => { throw new Error("Denied"); }}]) {
        const {button, source, display, selection, status} = fixture(clipboard);
        await button.click();
        assert.equal(display.focused, true);
        assert.equal(selection.source, source);
        assert.equal(status.textContent, "Select and copy manually");
        assert.equal(button.disabled, false);
    }
});

test("highlighting preserves exact source text and never creates source HTML", () => {
    function node(text = "") {
        return {
            value: text,
            children: [],
            append(child) { this.children.push(child); },
            replaceChildren(fragment) { this.value = ""; this.children = fragment.children; },
            get textContent() { return this.value + this.children.map(child => child.textContent).join(""); },
            set textContent(value) { this.value = value; this.children = []; },
        };
    }
    const text = "== 中文 ==\n{{模板|foo=[[條目|label]]}}\n<!-- {{hidden}} -->\n</code><script>alert(1)</script>\n''italic''\n";
    const display = node(text);
    const created = [];
    vm.runInNewContext(script, {
        document: {
            querySelector: () => null,
            querySelectorAll: selector => selector === "[data-newsletter-wikitext]" ? [display] : [],
            createDocumentFragment: () => node(),
            createTextNode: value => node(value),
            createElement(tag) { created.push(tag); return node(); },
        },
        navigator: {},
    });
    assert.equal(display.textContent, text);
    assert.ok(created.length > 0);
    assert.deepEqual([...new Set(created)], ["span"]);
    const classes = display.children.map(child => child.className).filter(Boolean);
    for (const kind of ["heading", "template", "link", "comment", "tag", "emphasis", "delimiter"]) {
        assert.ok(classes.includes(`newsletter-token-${kind}`));
    }
    const comment = display.children.find(child => child.className === "newsletter-token-comment");
    assert.equal(comment.textContent, "<!-- {{hidden}} -->");
});
