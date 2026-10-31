"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../../src/webservice/public_html/newsletter.js"), "utf8");

function fixture(clipboard) {
    const status = {textContent: ""};
    const editor = {
        value: "{{Original}}",
        focused: false,
        selected: false,
        focus() { this.focused = true; },
        select() { this.selected = true; },
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
    vm.runInNewContext(source, {
        document: {
            querySelector: () => null,
            querySelectorAll: () => [button],
            getElementById: () => editor,
        },
        navigator: {clipboard},
    });
    return {button, editor, status};
}

test("copy uses the current edited text and announces completion", async () => {
    let copied;
    const {button, editor, status} = fixture({writeText: async value => { copied = value; }});
    assert.equal(button.hidden, false);
    editor.value = "{{Changed|中文}}";
    await button.click();
    assert.equal(copied, editor.value);
    assert.equal(status.textContent, "Copied");
    assert.equal(button.disabled, false);
    assert.equal(editor.focused, false);
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
    vm.runInNewContext(source, {
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

test("missing or rejected clipboard selects editable text for manual copying", async () => {
    for (const clipboard of [undefined, {writeText: async () => { throw new Error("Denied"); }}]) {
        const {button, editor, status} = fixture(clipboard);
        await button.click();
        assert.equal(editor.focused, true);
        assert.equal(editor.selected, true);
        assert.equal(status.textContent, "Select and copy manually");
        assert.equal(button.disabled, false);
    }
});
