/* Exercise live clocks and polling offline with a controlled browser clock. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
    path.join(__dirname, "../../src/webservice/public_html/live.js"), "utf8",
);
const messages = {
    "duration.seconds": "{seconds} sec",
    "duration.minutes": "{minutes} min {seconds} sec",
    "time.minutes_ago": "({count} min ago)",
    "time.hours_ago": "({count} hr ago)",
    "time.minutes_ahead": "(in {count} min)",
    "time.hours_ahead": "(in {count} hr)",
    "time.soon": "(soon)",
    "time.just_now": "(just now)",
};
const base = Date.parse("2026-10-24T12:00:00Z");
const stamp = (offset) => new Date(base + offset).toISOString();

class Text {
    constructor(data) {
        this.nodeType = 3;
        this.nodeName = "#text";
        this.data = data;
    }
    get nextSibling() {
        return this.parent?.childNodes[this.parent.childNodes.indexOf(this) + 1] ?? null;
    }
    get textContent() { return this.data; }
    cloneNode() { return new Text(this.data); }
    remove() {
        if (this.parent) {
            this.parent.childNodes.splice(this.parent.childNodes.indexOf(this), 1);
            this.parent = null;
        }
    }
}

class Element {
    constructor(name, attributes = {}, text = "", children = []) {
        this.nodeType = 1;
        this.nodeName = name;
        this.values = { ...attributes };
        this.childNodes = [...(text ? [new Text(text)] : []), ...children];
        this.childNodes.forEach((child) => { child.parent = this; });
    }
    get id() { return this.values.id; }
    get hidden() { return this.hasAttribute("hidden"); }
    set hidden(value) {
        if (value) this.setAttribute("hidden", "");
        else this.removeAttribute("hidden");
    }
    get attributes() {
        return Object.entries(this.values).map(([name, value]) => ({ name, value }));
    }
    get dataset() {
        return Object.fromEntries(Object.entries(this.values)
            .filter(([key]) => key.startsWith("data-"))
            .map(([key, value]) => [
                key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), value,
            ]));
    }
    get firstChild() { return this.childNodes[0] ?? null; }
    get nextSibling() {
        return this.parent?.childNodes[this.parent.childNodes.indexOf(this) + 1] ?? null;
    }
    get textContent() { return this.childNodes.map((child) => child.textContent).join(""); }
    set textContent(value) {
        this.childNodes = value ? [new Text(value)] : [];
        this.childNodes.forEach((child) => { child.parent = this; });
    }
    getAttribute(name) { return this.values[name] ?? null; }
    hasAttribute(name) { return name in this.values; }
    setAttribute(name, value) { this.values[name] = value; }
    removeAttribute(name) { delete this.values[name]; }
    querySelectorAll(selector) {
        const attribute = selector.slice(1, -1);
        return this.childNodes.filter((child) => child.nodeType === 1).flatMap((child) => [
            ...(child.hasAttribute(attribute) ? [child] : []),
            ...child.querySelectorAll(selector),
        ]);
    }
    cloneNode() {
        return new Element(this.nodeName, this.values, "",
            this.childNodes.map((child) => child.cloneNode()));
    }
    insertBefore(node, cursor) {
        node.remove();
        const index = cursor ? this.childNodes.indexOf(cursor) : this.childNodes.length;
        this.childNodes.splice(index, 0, node);
        node.parent = this;
    }
    remove() {
        if (this.parent) {
            this.parent.childNodes.splice(this.parent.childNodes.indexOf(this), 1);
            this.parent = null;
        }
    }
}

function fixture(offset = 0, completed = false) {
    const clock = new Element("SPAN", {
        id: "elapsed",
        ...(completed ? {} : {
            "data-clock": "elapsed",
            "data-started-at": stamp(-10000),
            "data-duration-template": "({duration})",
        }),
    }, completed ? "(14 sec)" : "(10 sec…)");
    const popup = new Element("DIV", { id: "popover", popover: "auto" });
    const relative = new Element("SMALL", {
        "data-clock": "relative", "data-observed-at": stamp(75000),
    });
    const schedule = new Element("SPAN", { "data-schedule-time": "" }, "Next: ", [relative]);
    const notice = new Element("P", {
        id: "live-notice", hidden: "",
    }, "Reconnecting");
    const root = new Element("SECTION", {
        id: "tool-status", "data-server-time": stamp(offset),
        "data-clock-messages": JSON.stringify(messages),
        "data-schedule-expires": stamp(offset + 120000),
        "data-schedule-stale": "Schedule stale",
    }, "", [clock, popup, schedule, notice]);
    const uptime = new Element("SPAN", {
        id: "live-uptime", "data-clock": "uptime", "data-started-at": stamp(-100000),
        "data-uptime-template": "{days}d {hours}h {minutes}m {seconds}s",
        "data-uptime-unknown": "Uptime unavailable",
    });
    const document = {
        hidden: false,
        documentElement: { lang: "en" },
        roots: [root, uptime],
        getElementById(id) {
            const search = (node) => node.id === id ? node
                : (node.childNodes ?? []).map(search).find(Boolean);
            return this.roots.map(search).find(Boolean) ?? null;
        },
    };
    return { document, clock, popup, relative, schedule, notice, root, uptime };
}

function browser() {
    const state = fixture();
    const requests = [];
    const timers = new Map();
    const events = new Map();
    let now = 0;
    let nextTimer = 0;
    let incoming;
    state.document.addEventListener = (name, callback) => events.set(name, callback);
    vm.runInNewContext(source, {
        document: state.document,
        window: { addEventListener: (name, callback) => events.set(name, callback) },
        Node: { ELEMENT_NODE: 1, TEXT_NODE: 3 },
        Date, JSON, AbortController,
        performance: { now: () => now },
        setTimeout(callback, delay) {
            const id = ++nextTimer;
            timers.set(id, { at: now + delay, callback });
            return id;
        },
        clearTimeout: (id) => timers.delete(id),
        DOMParser: class { parseFromString() { return incoming.document; } },
        fetch(url, options) {
            return new Promise((resolve, reject) => {
                requests.push({ url, options, resolve, reject });
                options.signal.addEventListener("abort", () => reject(new Error("Aborted")));
            });
        },
    });
    const settle = async () => {
        for (let i = 0; i < 8; i++) await Promise.resolve();
    };
    return {
        ...state, requests, settle,
        async respond(next = fixture(now)) {
            incoming = next;
            requests.at(-1).resolve({
                ok: true, headers: { get: () => "text/html; charset=utf-8" },
                text: async () => "saved fragments",
            });
            await settle();
        },
        fire(name) { events.get(name)(); },
        jump(milliseconds) {
            now += milliseconds;
            for (const [id, timer] of [...timers]) {
                if (timers.has(id) && timer.at <= now) {
                    timers.delete(id);
                    timer.callback();
                }
            }
        },
    };
}

test("local clocks advance by elapsed time while server requests stay periodic", async () => {
    const page = browser();
    assert.equal(page.notice.hidden, true);
    await page.respond();
    assert.equal(page.notice.hidden, true);
    assert.equal(page.requests.length, 1);
    page.jump(1000);
    assert.equal(page.clock.textContent, "(11 sec…)");
    assert.equal(page.uptime.textContent, "0d 0h 1m 41s");
    page.jump(4200); // A delayed callback must catch up without losing seconds.
    assert.equal(page.clock.textContent, "(15 sec…)");
    assert.equal(page.requests.length, 1);
    page.jump(9800);
    assert.equal(page.requests.length, 2);
    assert.equal(page.relative.textContent, "(in 1 min)");
    page.jump(1000);
    assert.equal(page.relative.textContent, "(soon)");
});

test("synchronization keeps existing popovers and freezes completed timers", async () => {
    const page = browser();
    page.popup.open = true;
    page.document.activeElement = page.popup;
    await page.respond();
    page.jump(15000);
    await page.respond(fixture(15000, true));
    assert.equal(page.document.getElementById("popover"), page.popup);
    assert.equal(page.popup.open, true);
    assert.equal(page.document.activeElement, page.popup);
    assert.equal(page.clock.hasAttribute("data-clock"), false);
    const completed = page.clock.textContent;
    assert.equal(completed, "(14 sec)");
    page.jump(3000);
    assert.equal(page.clock.textContent, completed);
});

test("hidden tabs suspend work and resume immediately without overlapping fetches", async () => {
    const page = browser();
    page.document.hidden = true;
    page.fire("visibilitychange");
    page.jump(6000);
    assert.equal(page.requests.length, 1);
    assert.equal(page.clock.textContent, "(10 sec…)");
    page.document.hidden = false;
    page.fire("visibilitychange");
    assert.equal(page.requests.length, 1); // Wait for the aborted request's cleanup.
    await page.settle();
    page.jump(0);
    assert.equal(page.requests.length, 2);
    assert.equal(page.clock.textContent, "(16 sec…)");
    page.fire("online");
    page.fire("pageshow");
    assert.equal(page.requests.length, 2);
    await page.respond();
    page.jump(1000);
    assert.equal(page.requests.length, 2);
});

test("failed syncs retain observations, expire schedules, and recover", async () => {
    const page = browser();
    await page.respond();
    page.jump(15000);
    page.requests.at(-1).reject(new Error("Offline"));
    await page.settle();
    assert.equal(page.notice.textContent, "Reconnecting");
    assert.equal(page.notice.hidden, false);
    page.jump(106000);
    assert.equal(page.schedule.textContent, "Schedule stale");
    assert.equal(page.uptime.textContent, "Uptime unavailable");
    assert.equal(page.clock.textContent, "(2 min 11 sec…)");
    await page.respond();
    assert.equal(page.notice.hidden, true);
    assert.equal(page.schedule.hasAttribute("data-schedule-time"), true);
    assert.equal(page.uptime.textContent, "0d 0h 3m 41s");
});

test("malformed fragments do not replace received observations", async () => {
    const page = browser();
    await page.respond();
    page.jump(15000);
    const malformed = fixture(15000);
    malformed.root.setAttribute("data-server-time", "invalid");
    await page.respond(malformed);
    assert.equal(page.notice.textContent, "Reconnecting");
    assert.equal(page.notice.hidden, false);
    assert.equal(page.root.dataset.serverTime, stamp(0));
    page.jump(1000);
    assert.equal(page.clock.textContent, "(26 sec…)");
});
