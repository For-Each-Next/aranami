"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.resolve(__dirname, "../../src/webservice/public_html/copy.js"), "utf8").replace(/export \{[\s\S]*?\};?\s*$/, "");

function fixture(clipboard) {
    class Element {}
    class HTMLTextAreaElement extends Element {
        value = "{{Article history\n | maindateFA = 2026-11-01\n\n | maindateGA = 2019-02-04\n}}";
        focused = false;
        selected = false;
        focus() { this.focused = true; }
        select() { this.selected = true; }
    }
    const source = new HTMLTextAreaElement();
    const status = {textContent: ""};
    const button = new Element();
    Object.assign(button, {
        disabled: false,
        dataset: {copyTarget: "source", copySuccess: "Copied", copyFailure: "Select and copy manually"},
        closest(selector) { return selector.startsWith("button") ? button : {querySelector: () => status}; },
    });
    const attributes = new Set();
    const handlers = {};
    const module = {exports: {}};
    const context = {
        module,
        navigator: {clipboard},
        Element,
        HTMLTextAreaElement,
        document: {
            documentElement: {hasAttribute: name => attributes.has(name), setAttribute: name => attributes.add(name)},
            getElementById: id => id === "source" ? source : null,
            addEventListener(type, handler) { handlers[type] = handler; },
        },
    };
    vm.runInNewContext(script + "\ninitializeCopyControls();", context);
    return {button, source, status, click: () => handlers.click({target: button})};
}

test("manual suggestions copy their literal parameter code", async () => {
    const values = [];
    const view = fixture({writeText: async value => values.push(value)});
    view.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(values, [view.source.value]);
    assert.equal(view.status.textContent, "Copied");
    assert.equal(view.source.focused, false);
    assert.equal(view.button.disabled, false);
});

test("unavailable clipboard selects the unchanged source for manual copying", async () => {
    for (const clipboard of [undefined, {writeText: async () => { throw new Error("Denied"); }}]) {
        const view = fixture(clipboard);
        const original = view.source.value;
        view.click();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(view.source.value, original);
        assert.equal(view.source.focused, true);
        assert.equal(view.source.selected, true);
        assert.equal(view.status.textContent, "Select and copy manually");
        assert.equal(view.button.disabled, false);
    }
});

test("repeated clicks cannot queue concurrent copies", async () => {
    let finish;
    let writes = 0;
    const view = fixture({writeText: () => {
        writes++;
        return new Promise(resolve => { finish = resolve; });
    }});
    view.click();
    view.click();
    assert.equal(writes, 1);
    assert.equal(view.button.disabled, true);
    finish();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(view.button.disabled, false);
});
