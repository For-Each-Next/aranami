/* Verify personal hiding and incremental saved-result transport. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const script = fs.readFileSync(path.resolve(__dirname,
    "../../src/webservice/public_html/redirects.js"), "utf8");

function fixture({ storageDisabled = false, fetchImpl } = {}) {
    const handlers = {};
    const windowHandlers = {};
    const browserHandlers = {};
    const timers = new Map();
    let timerId = 0;
    const stored = new Map();
    const controls = { hidden: true };
    const show = { checked: false, disabled: true, focused: false,
        addEventListener(name, handler) { handlers[`show:${name}`] = handler; },
        focus() { this.focused = true; } };
    const restore = { disabled: true,
        addEventListener(name, handler) { handlers[`restore:${name}`] = handler; } };
    const check = { disabled: true,
        addEventListener(name, handler) { handlers[`check:${name}`] = handler; } };
    const warning = { hidden: true };
    const checkError = { hidden: true }, checkDone = { hidden: true };
    const document = { hidden: false, activeElement: null,
        addEventListener(name, handler) { handlers[name] = handler; },
        querySelector() { return browser; } };
    function results(identity, ids) {
        const node = { dataset: { identity }, contains: value => value === node };
        node.rows = ids.map(id => {
            const button = { hidden: true, disabled: true, textContent: "", attributes: {},
                setAttribute(name, value) { this.attributes[name] = value; },
                closest(selector) { return selector === "[data-redirect-hide]" ? button : row; } };
            const row = { dataset: { redirectId: id, talkId: id }, hidden: false,
                classList: { toggle() {} }, querySelector(selector) {
                    return selector === "h2" ? { textContent: `Game ${id}` } : button;
                } };
            row.button = button;
            return row;
        });
        node.replaceWith = replacement => { current = replacement; };
        return node;
    }
    let current = results("first", ["1"]);
    let replacement = results("second", ["1", "2"]);
    let fetches = 0;
    let lastSignal;
    const browser = { dataset: { hideLabel: "Hide", restoreLabel: "Restore", bannerAliases: '["Template:WikiProject Video games","Template:VG"]' },
        querySelector(selector) { return ({
            "[data-redirect-controls]": controls, "#redirect-show-hidden": show,
            "[data-redirect-refresh-error]": warning, "#redirect-results": current,
            "[data-redirect-restore-all]": restore,
            "[data-redirect-check]": check, "[data-redirect-check-error]": checkError,
            "[data-redirect-check-done]": checkDone,
        })[selector]; },
        querySelectorAll() { return current.rows; },
        addEventListener(name, handler) { browserHandlers[name] = handler; } };
    const localStorage = {
        getItem(key) { if (storageDisabled) throw new Error("Blocked"); return stored.get(key) ?? null; },
        setItem(key, value) { if (storageDisabled) throw new Error("Blocked"); stored.set(key, value); },
    };
    vm.runInNewContext(script, {
        document, localStorage, AbortController, AbortSignal, URLSearchParams,
        window: { document, addEventListener(name, handler) { windowHandlers[name] = handler; } },
        location: { href: "https://aranami.toolforge.org/redirects/?lang=en" },
        DOMParser: class { parseFromString() { return { querySelector: () => replacement }; } },
        fetch: async (...args) => {
            fetches++; lastSignal = args[1].signal;
            if (fetchImpl) return fetchImpl(...args);
            return { ok: true, headers: { get: () => "text/html; charset=utf-8" }, text: async () => "saved" };
        },
        setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; },
        clearTimeout(id) { timers.delete(id); },
    });
    return {
        get rows() { return current.rows; },
        get identity() { return current.dataset.identity; },
        get fetches() { return fetches; },
        get signal() { return lastSignal; },
        get stored() { return stored.get("aranami-redirects-hidden-v1"); },
        warning, checkError, checkDone, document,
        hide() { browserHandlers.click({ target: current.rows[0].button }); },
        show() { show.checked = !show.checked; handlers["show:change"](); },
        restore() { handlers["restore:click"](); },
        async check() { handlers["check:click"](); await new Promise(resolve => setImmediate(resolve)); },
        visibility(hidden) { document.hidden = hidden; handlers.visibilitychange(); },
        stop() { windowHandlers.pagehide(); },
        async poll() {
            const next = [...timers.values()].find(item => item.delay === 30_000);
            if (!next) throw new Error("No refresh scheduled");
            for (const [id, item] of timers) if (item === next) timers.delete(id);
            next.callback();
            await new Promise(resolve => setImmediate(resolve));
        },
        pendingPolls: () => [...timers.values()].filter(item => item.delay === 30_000).length,
    };
}

test("hiding survives incremental replacement and can be restored", async () => {
    const view = fixture();
    view.hide();
    assert.equal(view.rows[0].hidden, true);
    assert.deepEqual(JSON.parse(view.stored), ["1"]);
    await view.poll();
    assert.equal(view.identity, "second");
    assert.equal(view.rows.length, 2);
    assert.equal(view.rows[0].hidden, true);
    assert.equal(view.rows[1].hidden, false);
    view.show();
    assert.equal(view.rows[0].hidden, false);
    assert.equal(view.rows[0].button.textContent, "Restore");
    view.restore();
    assert.deepEqual(JSON.parse(view.stored), []);
    view.stop();
});

test("explicit check hides only confirmed banners and polling retains the result", async () => {
    const view = fixture({ fetchImpl: async (url) => {
        if (String(url).includes("zh.wikipedia.org")) {
            const params = new URL(url).searchParams;
            assert.equal(params.get("pageids"), "1");
            assert.equal(params.get("tltemplates"), "Template:WikiProject Video games|Template:VG");
            return { ok: true, json: async () => ({ query: { pages: [
                { pageid: 1, templates: [{ title: "Template:VG" }] },
            ] } }) };
        }
        return { ok: true, headers: { get: () => "text/html" }, text: async () => "saved" };
    } });
    await view.check();
    assert.equal(view.rows[0].hidden, true);
    assert.equal(view.checkDone.hidden, false);
    assert.equal(view.stored, undefined);
    await view.poll();
    assert.equal(view.rows[0].hidden, true);
    assert.equal(view.rows[1].hidden, false);
    view.stop();
});

test("failed banner checks leave unconfirmed pages visible", async () => {
    const view = fixture({ fetchImpl: async () => { throw new Error("Offline"); } });
    await view.check();
    assert.equal(view.rows[0].hidden, false);
    assert.equal(view.checkError.hidden, false);
    assert.equal(view.checkDone.hidden, true);
    view.stop();
});

test("storage restrictions still allow reversible hiding during this visit", () => {
    const view = fixture({ storageDisabled: true });
    view.hide();
    assert.equal(view.rows[0].hidden, true);
    view.restore();
    assert.equal(view.rows[0].hidden, false);
    view.stop();
});

test("failed refresh retains prior results and surfaces availability", async () => {
    const view = fixture({ fetchImpl: async () => { throw new Error("Offline"); } });
    await view.poll();
    assert.equal(view.identity, "first");
    assert.equal(view.warning.hidden, false);
    assert.equal(view.pendingPolls(), 1);
    view.stop();
});

test("hidden pages stop polling and page shutdown cancels an active request", async () => {
    const view = fixture({ fetchImpl: () => new Promise(() => {}) });
    view.visibility(true);
    assert.equal(view.pendingPolls(), 0);
    assert.equal(view.fetches, 0);
    view.visibility(false);
    await view.poll();
    assert.equal(view.fetches, 1);
    assert.equal(view.signal.aborted, false);
    view.stop();
    assert.equal(view.signal.aborted, true);
    assert.equal(view.pendingPolls(), 0);
});
