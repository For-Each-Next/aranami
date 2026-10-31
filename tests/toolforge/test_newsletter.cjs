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

test("issue selection has no automatic change submission", () => {
    assert.equal(script.includes("requestSubmit"), false);
    assert.equal(script.includes('input[name="issue"]'), false);
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

function calendarFixture(locale = "en") {
    function node() {
        return {
            children: [], attrs: {}, handlers: {}, textContent: "", hidden: true,
            append(child) { this.children.push(child); },
            replaceChildren() { this.children = []; },
            setAttribute(name, value) { this.attrs[name] = value; },
            addEventListener(name, handler) { this.handlers[name] = handler; },
            focus() { this.focused = true; },
        };
    }
    const input = {value: "2026-10-30", hidden: false};
    const fields = Object.fromEntries(["selected", "month", "previous", "next", "weekdays", "days"].map(key => [`[data-calendar-${key}]`, node()]));
    fields.summary = node();
    const calendar = node();
    calendar.dataset = {dates: JSON.stringify(["2026-10-28", "2026-10-30", "2026-11-03"])};
    calendar.querySelector = selector => fields[selector];
    vm.runInNewContext(script, {
        document: {
            documentElement: {lang: locale},
            querySelector: () => calendar,
            querySelectorAll: () => [],
            getElementById: () => input,
            createElement: () => node(),
        },
        Intl, Date,
    });
    const buttons = () => fields["[data-calendar-days]"].children.flatMap(row => row.children.flatMap(cell => cell.children));
    return {input, fields, calendar, buttons};
}

test("calendar starts on Sunday and disables every unavailable date", () => {
    const {input, fields, calendar, buttons} = calendarFixture();
    assert.equal(calendar.hidden, false);
    assert.equal(input.hidden, true);
    assert.equal(input.type, "hidden");
    assert.deepEqual(fields["[data-calendar-weekdays]"].children[0].children.map(day => day.textContent), ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]);
    assert.deepEqual(buttons().filter(button => !button.disabled).map(button => button.attrs["aria-label"]), ["2026-10-28", "2026-10-30"]);
    assert.equal(buttons().find(button => button.textContent === "29").disabled, true);
    assert.equal(fields["[data-calendar-previous]"].disabled, true);
    assert.equal(fields["[data-calendar-next]"].disabled, false);
    const firstRow = fields["[data-calendar-days]"].children[0].children;
    assert.equal(firstRow.findIndex(cell => cell.children.length > 0), 4);
});

test("calendar browses months and selects dates without submitting", () => {
    const {input, fields, calendar, buttons} = calendarFixture("zh-Hant");
    fields["[data-calendar-next]"].handlers.click();
    assert.equal(fields["[data-calendar-next]"].disabled, true);
    assert.equal(fields["[data-calendar-previous]"].disabled, false);
    assert.deepEqual(buttons().filter(button => !button.disabled).map(button => button.attrs["aria-label"]), ["2026-11-03"]);
    const target = buttons().find(button => button.attrs["aria-label"] === "2026-11-03");
    target.handlers.click();
    assert.equal(input.value, "2026-11-03");
    assert.equal(fields["[data-calendar-selected]"].textContent, "2026-11-03");
    assert.equal(calendar.open, false);
    assert.equal(fields.summary.focused, true);
    assert.equal(buttons().find(button => button.attrs["aria-label"] === "2026-11-03").attrs["aria-pressed"], "true");
    fields["[data-calendar-previous]"].handlers.click();
    assert.equal(fields["[data-calendar-month]"].textContent.includes("10"), true);
});
