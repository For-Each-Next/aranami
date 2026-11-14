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
    "duration.hours": "{hours} hr {minutes} min {seconds} sec",
    "duration.days": "{days} d {hours} hr {minutes} min {seconds} sec",
    "time.minutes_ago": "({count}m ago)",
    "time.hours_ago": "({count}h ago)",
    "time.days_ago": "({count}d ago)",
    "time.weeks_ago": "({count}w ago)",
    "time.months_ago": "({count}mo ago)",
    "time.years_ago": "({count}yr ago)",
    "time.minutes_ahead": "({count}m from now)",
    "time.hours_ahead": "({count}h from now)",
    "time.days_ahead": "({count}d from now)",
    "time.weeks_ahead": "({count}w from now)",
    "time.months_ahead": "({count}mo from now)",
    "time.years_ahead": "({count}yr from now)",
    "time.soon": "(within 1m)",
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
    get open() { return this.hasAttribute("open"); }
    set open(value) {
        if (value) this.setAttribute("open", "");
        else this.removeAttribute("open");
    }
    get isConnected() { return Boolean(this.parent); }
    closest(selector) {
        if (selector === 'a[aria-haspopup="dialog"],button[commandfor]'
            && ((this.nodeName === "BUTTON" && this.hasAttribute("commandfor"))
                || (this.nodeName === "A" && this.getAttribute("aria-haspopup") === "dialog"))) return this;
        if (selector === "dialog[open]" && this.nodeName === "DIALOG" && this.open) return this;
        return this.parent?.closest(selector) ?? null;
    }
    showModal() { this.open = true; this.modalOpens = (this.modalOpens ?? 0) + 1; }
    close() { this.open = false; }
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

function fixture(offset = 0, completed = false, logs = false, resources = false, footerOnly = false, datasetUpdates = false) {
    const subject = "Updating records for October 20, 2026";
    const button = new Element("A", {
        id: "status-button", href: "#popover", "aria-controls": "popover", "aria-haspopup": "dialog",
    }, "Details…");
    const statusLabel = new Element("SPAN", {
        id: "status-label",

    }, completed ? "No update" : subject);
    const statusLine = new Element("P", {
        id: "status-line", class: "routine-status", "data-status": completed ? "no_update" : "running",
    }, "", [statusLabel, button]);
    const clock = new Element("SPAN", {
        id: "elapsed",
        ...(completed ? {} : {
            "data-clock": "elapsed",
            "data-started-at": stamp(-10000),
            "data-duration-template": "({duration})",
        }),
    }, completed ? "(14.30 sec)" : "(10 sec…)");
    const popup = new Element("DIALOG", { id: "popover" });
    const relative = new Element("SMALL", {
        "data-clock": "relative", "data-observed-at": stamp(75000),
    });
    const schedule = new Element("SPAN", { "data-schedule-time": "" }, "Next ", [relative]);
    const server = new Element("P", {
        id: "server-state", "data-schedule-time": "", "data-stale-text": "Server unavailable",
    }, "Running");
    const progress = new Element("LI", {  }, "Fetching pageviews (2,000/13,500)");
    const notice = new Element("P", {
        id: logs ? "live-server-notice" : "live-notice", hidden: "",
    }, "Reconnecting");
    const clockAttributes = {
        "data-server-time": stamp(offset),
        "data-clock-messages": JSON.stringify(messages),
        "data-schedule-expires": stamp(offset + 120000),
        "data-schedule-stale": "Schedule stale",
    };
    const root = new Element("SECTION", {
        id: logs ? "live-server-status" : "tool-status", ...clockAttributes,
    }, "", logs ? [server, notice] : [
        statusLine, clock, popup, schedule, notice, ...(completed ? [] : [progress]),
    ]);
    const uptime = new Element("SPAN", {
        ...clockAttributes,
        id: "live-uptime", "data-clock": "uptime", "data-started-at": stamp(-100000),
        "data-uptime-template": "{days}d {hours}h {minutes}m {seconds}s",
        "data-uptime-compact-template": "{duration}",
        "data-uptime-unknown": "Uptime unavailable",
    });
    const memory = new Element("TD", {
        id: "memory-usage", "data-schedule-time": "", "data-stale-text": "Unknown",
    }, "100.0 MiB");
    const runtime = new Element("DIV", { id: "server-runtime" }, "", [memory]);
    const datasetClock = new Element("SPAN", {id: "dataset-elapsed", "data-clock": "dataset-elapsed", "data-started-at": stamp(-65000), "data-clock-expires": stamp(120000), "data-clock-unknown": "Unknown"});
    const datasetHeading = new Element("H2", {id: "dataset-updates-current-heading", "data-observation-expires": stamp(120000), "data-stale-text": "Planned updates"}, "Updating datasets");
    const datasetBody = new Element("DIV", {id: "dataset-update-body", "data-observation-expires": stamp(120000), "data-stale-text": "Progress unknown"}, "", [datasetClock]);
    const previousUpdates = new Element("SECTION", {id: "dataset-updates-previous"}, "Previous files");
    const currentUpdates = new Element("SECTION", {id: "dataset-updates-current"}, "", [datasetHeading, datasetBody]);
    const document = {
        hidden: false,
        documentElement: { lang: "en" },
        roots: footerOnly ? [uptime] : [root, uptime, ...(resources ? [runtime] : []), ...(datasetUpdates ? [previousUpdates, currentUpdates] : [])],
        getElementById(id) {
            const search = (node) => node.id === id ? node
                : (node.childNodes ?? []).map(search).find(Boolean);
            return this.roots.map(search).find(Boolean) ?? null;
        },
    };
    return { document, button, statusLabel, statusLine, clock, popup, relative, schedule, server, progress, notice, root, uptime, runtime, memory, datasetClock, datasetHeading, datasetBody, previousUpdates, currentUpdates };
}

function browser(logs = false, resources = false, options = {}) {
    const state = fixture(0, false, logs, resources, options.footerOnly, options.datasetUpdates);
    if (options.fastResources) state.root.setAttribute("data-refresh-interval", "1000");
    const requests = [];
    const warnings = [];
    const sockets = [];
    const documents = new Map();
    const timers = new Map();
    const events = new Map();
    let now = 0;
    let nextTimer = 0;
    const html = (next) => {
        const token = `saved fragments ${documents.size}`;
        documents.set(token, next.document);
        return token;
    };
    class Socket {
        static OPEN = 1;
        readyState = 0;
        closes = 0;
        constructor(url) {
            this.url = url;
            sockets.push(this);
        }
        open() { this.readyState = Socket.OPEN; this.onopen?.(); }
        message(data) { this.onmessage?.({ data }); }
        close() { this.readyState = 3; this.closes++; }
        disconnect() { this.readyState = 3; this.onclose?.(); }
        fail() { this.readyState = 3; this.onerror?.(); }
    }
    state.document.documentElement.lang = options.language ?? "en";
    state.document.addEventListener = (name, callback) => events.set(name, callback);
    vm.runInNewContext(source, {
        document: state.document,
        window: {
            addEventListener: (name, callback) => events.set(name, callback),
            location: { protocol: options.protocol ?? "https:", host: "aranami.toolforge.org", hash: options.hash ?? "" },
        },
        Node: { ELEMENT_NODE: 1, TEXT_NODE: 3 },
        Date, JSON, AbortController,
        WebSocket: options.websocket ? Socket : undefined,
        Math: Object.assign(Object.create(Math), { random: () => options.random ?? 0.5 }),
        console: { warn: (...args) => warnings.push(args) },
        performance: { now: () => now },
        setTimeout(callback, delay) {
            const id = ++nextTimer;
            timers.set(id, { at: now + delay, callback });
            return id;
        },
        clearTimeout: (id) => timers.delete(id),
        DOMParser: class { parseFromString(value) { return documents.get(value); } },
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
    const envelope = (next, overrides = {}) => ({
        schema: 1, type: "status", instance: "a".repeat(32),
        sequence: Number.isFinite(Date.parse(next.root.dataset.serverTime)) ? Date.parse(next.root.dataset.serverTime) - base + 1 : now + 1,
        store_id: "b".repeat(32), revision: 1,
        observed_at: stamp(now), html: html(next), ...overrides,
    });
    return {
        ...state, requests, warnings, sockets, settle, envelope,
        async respond(next = fixture(now, false, logs, resources, options.footerOnly, options.datasetUpdates), response = {}) {
            requests.at(-1).resolve({
                ok: true, status: 200, headers: { get: (key) => key === "Content-Type" ? "application/json; charset=utf-8" : null },
                json: async () => envelope(next),
                ...response,
            });
            await settle();
        },
        frame(frame, socket = sockets.at(-1)) {
            socket.message(typeof frame === "string" ? frame : JSON.stringify({schema: 1, instance: "a".repeat(32), sequence: 1, store_id: null, revision: null, observed_at: stamp(0), ...frame}));
        },
        push(next = fixture(now, false, logs, resources, options.footerOnly, options.datasetUpdates), socket = sockets.at(-1)) {
            socket.message(JSON.stringify(envelope(next)));
        },
        fire(name, event) { events.get(name)(event); },
        elapse(milliseconds) { now += milliseconds; },
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

test("dialog links open dialogs and restore the exact invoker after dismissal", async () => {
    const page = browser();
    page.button.focus = () => { page.document.activeElement = page.button; };
    let prevented = 0;
    page.fire("click", { target: page.button, preventDefault() { prevented++; } });
    assert.equal(page.popup.open, true);
    assert.equal(page.popup.modalOpens, 1);
    await page.respond();
    assert.equal(page.popup.open, true); // A status update cannot close a modal.
    page.fire("click", { target: page.button, preventDefault() { prevented++; } });
    assert.equal(page.popup.modalOpens, 1);
    const close = new Element("BUTTON", { commandfor: "popover", command: "close" });
    page.fire("click", { target: close, preventDefault() { prevented++; } });
    assert.equal(page.popup.open, false);
    page.fire("close", { target: page.popup });
    assert.equal(page.document.activeElement, page.button);
    assert.equal(prevented, 3);
});

test("direct report fragments open their dialog and ignore other anchors", () => {
    const direct = browser(false, false, {hash: "#popover"});
    assert.equal(direct.popup.open, true);
    direct.fire("hashchange");
    assert.equal(direct.popup.modalOpens, 1);
    const other = browser(false, false, {hash: "#status-label"});
    assert.equal(other.popup.open, false);
});

test("modified dialog links retain normal browser navigation", () => {
    for (const keys of [{ctrlKey: true}, {metaKey: true}, {shiftKey: true}, {altKey: true}, {button: 1}]) {
        const page = browser();
        page.fire("click", { target: page.button, ...keys, preventDefault() { assert.fail("modified click intercepted"); } });
        assert.equal(page.popup.open, false);
    }
});

test("Escape/native close restores focus without a close-button click", () => {
    const page = browser();
    page.button.focus = () => { page.document.activeElement = page.button; };
    page.fire("click", { target: page.button, preventDefault() {} });
    page.document.activeElement = page.popup;
    page.popup.close();
    page.fire("close", { target: page.popup });
    assert.equal(page.document.activeElement, page.button);
});

test("Tab and Shift+Tab wrap within the currently open dialog", () => {
    const page = browser();
    const close = new Element("BUTTON");
    const scroll = new Element("DIV", { tabindex: "0" });
    const lastLink = new Element("A", { href: "https://zh.wikipedia.org" });
    const hidden = new Element("BUTTON");
    const disabled = new Element("BUTTON");
    for (const control of [close, scroll, lastLink, hidden, disabled]) {
        page.popup.insertBefore(control, null);
        control.tabIndex = 0;
        control.getClientRects = () => [{}];
        control.focus = () => { page.document.activeElement = control; };
    }
    hidden.getClientRects = () => [];
    disabled.disabled = true;
    page.popup.querySelectorAll = () => [close, scroll, lastLink, hidden, disabled];
    page.popup.open = true;
    let prevented = 0;
    page.document.activeElement = close;
    page.fire("keydown", {
        target: close, key: "Tab", shiftKey: true, preventDefault() { prevented++; },
    });
    assert.equal(page.document.activeElement, lastLink);
    page.fire("keydown", {
        target: lastLink, key: "Tab", shiftKey: false, preventDefault() { prevented++; },
    });
    assert.equal(page.document.activeElement, close);
    page.document.activeElement = scroll;
    page.fire("keydown", {
        target: scroll, key: "Tab", shiftKey: false, preventDefault() { prevented++; },
    });
    assert.equal(prevented, 2); // Interior navigation is left to the browser.
    page.popup.open = false;
    page.fire("keydown", {
        target: close, key: "Tab", shiftKey: true, preventDefault() { prevented++; },
    });
    assert.equal(prevented, 2); // Background controls are never trapped.
});

test("running clocks use whole seconds across day and hour boundaries", () => {
    for (const [seconds, expected] of [
        [0.44, "(0 sec…)"],
        [32.89, "(32 sec…)"],
        [59.999, "(59 sec…)"],
        [60, "(1 min 0 sec…)"],
        [725.3, "(12 min 5 sec…)"],
        [90061, "(1 d 1 hr 1 min 1 sec…)"],
    ]) {
        const page = browser();
        page.clock.setAttribute("data-started-at", stamp(1000 - seconds * 1000));
        page.jump(1000);
        assert.equal(page.clock.textContent, expected);
    }
});

test("relative clocks use compact units at minute through year boundaries", () => {
    for (const [seconds, expected] of [
        [-59, "(just now)"],
        [-60, "(1m ago)"],
        [-3599, "(59m ago)"],
        [-3600, "(1h ago)"],
        [-86399, "(23h ago)"],
        [-86400, "(1d ago)"],
        [-259200, "(3d ago)"],
        [-604799, "(6d ago)"],
        [-604800, "(1w ago)"],
        [-2629799, "(4w ago)"],
        [-2629800, "(1mo ago)"],
        [-5259600, "(2mo ago)"],
        [-31557599, "(11mo ago)"],
        [-31557600, "(1yr ago)"],
        [-63115200, "(2yr ago)"],
        [59, "(within 1m)"],
        [60, "(1m from now)"],
        [3600, "(1h from now)"],
        [86399, "(23h from now)"],
        [86400, "(1d from now)"],
        [259200, "(3d from now)"],
        [604799, "(6d from now)"],
        [604800, "(1w from now)"],
        [2629799, "(4w from now)"],
        [2629800, "(1mo from now)"],
        [5259600, "(2mo from now)"],
        [31557599, "(11mo from now)"],
        [31557600, "(1yr from now)"],
        [63115200, "(2yr from now)"],
    ]) {
        const page = browser();
        page.relative.setAttribute("data-observed-at", stamp(1000 + seconds * 1000));
        page.jump(1000);
        assert.equal(page.relative.textContent, expected);
    }
});

test("inline relative notes omit localized parentheses", async () => {
    for (const [label, expected] of [
        ["({count}d ago)", "3d ago"],
        ["（{count}日前）", "3日前"],
    ]) {
        const page = browser();
        const next = fixture();
        next.relative.setAttribute("data-relative-inline", "");
        next.relative.setAttribute("data-observed-at", stamp(-259200000));
        next.root.setAttribute("data-clock-messages", JSON.stringify({
            ...messages, "time.days_ago": label,
        }));
        await page.respond(next);
        assert.equal(page.relative.textContent, expected);
        page.jump(1000);
        assert.equal(page.relative.textContent, expected);
    }
});

test("relative clocks use English abbreviations and localized Chinese units", async () => {
    const catalog = JSON.parse(fs.readFileSync(
        path.join(__dirname, "../../src/webservice/messages.json"), "utf8",
    ));
    const units = {
        "en": {minutes: "m", hours: "h", days: "d", weeks: "w", months: "mo", years: "yr"},
        "zh-hans": {minutes: "分钟", hours: "小时", days: "日", weeks: "周", months: "月", years: "年"},
        "zh-hant": {minutes: "分鐘", hours: "小時", days: "日", weeks: "週", months: "月", years: "年"},
    };
    for (const language of ["en", "zh-hans", "zh-hant"]) {
        const labels = Object.fromEntries(Object.entries(catalog)
            .filter(([key]) => key.startsWith("time.") || key.startsWith("duration."))
            .map(([key, translations]) => [key, translations[language]]));
        for (const [unit, minutes] of [
            ["minutes", 1], ["hours", 60], ["days", 1440],
            ["weeks", 10080], ["months", 43830], ["years", 525960],
        ]) {
            const suffix = units[language][unit];
            for (const direction of ["ago", "ahead"]) {
                const page = browser(false, false, { language });
                const next = fixture();
                const value = stamp(minutes * 60000 * (direction === "ago" ? -1 : 1));
                next.relative.setAttribute("data-observed-at", value);
                const label = language === "en"
                    ? `({count}${suffix} ${direction === "ago" ? "ago" : "from now"})`
                    : `（{count}${suffix}${direction === "ago" ? "前" : language === "zh-hant" ? "後" : "后"}）`;
                next.root.setAttribute("data-clock-messages", JSON.stringify(labels));
                await page.respond(next);
                assert.equal(page.relative.textContent, label.replace("{count}", "1"));
                assert.equal(page.relative.dataset.observedAt, value);
            }
        }
    }
});

test("clock snapshots require labels for every relative-age unit", async () => {
    for (const key of ["time.weeks_ago", "time.months_ahead", "time.years_ago"]) {
        const page = browser(false, false, { websocket: true });
        const next = fixture();
        const incomplete = {...messages};
        delete incomplete[key];
        next.root.setAttribute("data-clock-messages", JSON.stringify(incomplete));
        page.push(next);
        assert.equal(page.warnings[0][1].failure, "clock");
        assert.equal(page.root.dataset.clockMessages, JSON.stringify(messages));
    }
});

test("stale samples retain permanent boot facts while server state expires", async () => {
    const page = browser(true);
    const age = new Element("SMALL", {
        "data-clock": "relative", "data-relative-inline": "",
        "data-observed-at": stamp(-259200000),
    });
    const boot = new Element("SPAN", { id: "server-start" },
        " (2026-10-21 12:00:00, ", [age, new Text(")")]);
    page.root.insertBefore(boot, page.notice);
    page.jump(1000);
    assert.equal(boot.textContent, " (2026-10-21 12:00:00, 3d ago)");
    page.jump(120000);
    await page.settle();
    assert.equal(boot.textContent, " (2026-10-21 12:00:00, 3d ago)");
    assert.equal(page.server.textContent, "Server unavailable");
});

test("local clocks advance by elapsed time while server requests stay periodic", async () => {
    const page = browser();
    assert.equal(page.notice.hidden, true);
    await page.respond();
    assert.equal(page.notice.hidden, true);
    assert.equal(page.requests.length, 1);
    page.jump(1000);
    assert.equal(page.clock.textContent, "(11 sec…)");
    assert.equal(page.uptime.textContent, "1 min 41 sec");
    page.jump(3200); // A delayed callback must catch up without losing seconds.
    assert.equal(page.clock.textContent, "(14 sec…)");
    assert.equal(page.requests.length, 1);
    page.jump(800);
    assert.equal(page.requests.length, 2);
    await page.respond();
    assert.equal(page.clock.textContent, "(15 sec…)");
    page.jump(10000);
    assert.equal(page.requests.length, 3);
    assert.equal(page.relative.textContent, "(1m from now)");
    page.jump(1000);
    assert.equal(page.relative.textContent, "(within 1m)");
});

test("synchronization keeps existing modal dialogs and freezes completed timers", async () => {
    const page = browser();
    page.popup.open = true;
    page.document.activeElement = page.button;
    await page.respond();
    page.jump(5000);
    await page.respond(fixture(5000, true));
    assert.equal(page.document.getElementById("popover"), page.popup);
    assert.equal(page.popup.open, true);
    assert.equal(page.document.activeElement, page.button);
    assert.equal(page.document.getElementById("status-button"), page.button);
    assert.equal(page.statusLabel.textContent, "No update");
    assert.equal(page.statusLine.getAttribute("data-status"), "no_update");
    assert.equal(page.button.textContent, "Details…");
    assert.equal(page.clock.hasAttribute("data-clock"), false);
    const completed = page.clock.textContent;
    assert.equal(completed, "(14.30 sec)");
    page.jump(3000);
    assert.equal(page.clock.textContent, completed);
    assert.equal(page.statusLabel.textContent, "No update");
});

test("live outcomes update status text and styling state without replacing labels or open dialogs", async () => {
    const page = browser();
    page.popup.open = true;
    page.document.activeElement = page.button;
    for (const [index, state] of [
        "failed", "queued", "deferred", "unconfirmed", "unknown", "updated",
    ].entries()) {
        const next = fixture(index * 5000, true);
        next.statusLine.setAttribute("data-status", state);
        next.statusLabel.textContent = state;
        await page.respond(next);
        assert.equal(page.statusLine.getAttribute("data-status"), state);
        assert.equal(page.statusLabel.textContent, state);
        assert.equal(page.document.getElementById("status-line"), page.statusLine);
        assert.equal(page.document.getElementById("status-label"), page.statusLabel);
        assert.deepEqual(page.statusLine.childNodes, [page.statusLabel, page.button]);
        assert.equal(page.document.activeElement, page.button);
        assert.equal(page.popup.open, true);
        page.jump(5000);
    }
});

test("subject labels and phase counts synchronize without replacing buttons", async () => {
    const page = browser();
    page.popup.open = true;
    page.document.activeElement = page.button;
    assert.equal(page.statusLabel.textContent, "Updating records for October 20, 2026");
    assert.equal(page.button.textContent, "Details…");
    assert.equal(page.progress.textContent, "Fetching pageviews (2,000/13,500)");
    await page.respond();
    const labelNode = page.statusLabel.firstChild;
    page.jump(1000);
    assert.equal(page.statusLabel.firstChild, labelNode);
    assert.equal(page.progress.textContent, "Fetching pageviews (2,000/13,500)");
    page.jump(4000);
    assert.equal(page.requests.length, 2);
    const next = fixture(5000);
    const subject = "Updating records for October 21, 2026";
    next.statusLabel.textContent = subject;
    next.progress.textContent = "Fetching pageviews (3,000/13,500)";
    await page.respond(next);
    assert.equal(page.statusLabel.textContent, subject);
    assert.equal(page.button.textContent, "Details…");
    assert.equal(page.progress.textContent, "Fetching pageviews (3,000/13,500)");
    assert.equal(page.document.getElementById("status-button"), page.button);
    assert.equal(page.document.activeElement, page.button);
    assert.equal(page.popup.open, true);
    assert.equal(page.button.getAttribute("aria-controls"), "popover");
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
    assert.equal(page.warnings.length, 0);
    assert.equal(page.notice.hidden, true);
    assert.equal(page.clock.textContent, "(16 sec…)");
    page.fire("online");
    page.fire("pageshow");
    assert.equal(page.requests.length, 2);
    await page.respond();
    page.jump(1000);
    assert.equal(page.requests.length, 2);
});

test("focus synchronizes an idle visible page before delayed timers run", async () => {
    const page = browser();
    await page.respond();
    page.elapse(61000); // Browser sleep can delay timer delivery without hiding the tab.
    page.fire("focus");
    assert.equal(page.requests.length, 2);
    assert.equal(page.clock.textContent, "(1 min 11 sec…)");
    page.fire("focus");
    assert.equal(page.requests.length, 2);
    await page.respond();
    assert.equal(page.notice.hidden, true);
});

test("focus retires an overdue request and retries after its cleanup", async () => {
    const page = browser();
    page.elapse(61000);
    page.fire("focus");
    assert.equal(page.requests[0].options.signal.aborted, true);
    assert.equal(page.requests.length, 1);
    await page.settle();
    assert.equal(page.notice.dataset.liveError, "timeout");
    page.jump(0);
    assert.equal(page.requests.length, 2);
    await page.respond();
    assert.equal(page.notice.hidden, true);
    assert.equal(page.notice.hasAttribute("data-live-error"), false);
    page.jump(5000);
    assert.equal(page.requests.length, 3);
});

test("timeouts and network errors back off, report transitions, and recover", async () => {
    const page = browser();
    page.jump(10000);
    await page.settle();
    assert.equal(page.requests[0].options.signal.aborted, true);
    assert.equal(page.notice.hidden, true); // A fresh rendered status survives one failed check.
    assert.equal(page.warnings[0][1].failure, "timeout");
    page.jump(9999);
    assert.equal(page.requests.length, 1);
    page.jump(1);
    assert.equal(page.requests.length, 2);
    page.requests.at(-1).reject(new Error("Offline"));
    await page.settle();
    assert.equal(page.notice.hidden, true);
    assert.equal(page.warnings.length, 2);
    page.jump(20000);
    assert.equal(page.requests.length, 3);
    page.requests.at(-1).reject(new Error("Still offline"));
    await page.settle();
    assert.equal(page.warnings.length, 2); // Repeated failures do not flood the console.
    page.jump(30000);
    assert.equal(page.requests.length, 4);
    assert.equal(page.notice.dataset.liveError, "network");
    await page.respond();
    assert.equal(page.notice.hidden, true);
    assert.equal(page.notice.hasAttribute("data-live-error"), false);
    page.jump(5000);
    assert.equal(page.requests.length, 5);
});

test("HTTP, response, and render failures have separate bounded diagnostics", async () => {
    for (const [response, failure, status] of [
        [{ ok: false, status: 503 }, "http", 503],
        [{ headers: { get: () => "text/plain" } }, "response"],
        [{ json: async () => { throw new Error("Body unavailable"); } }, "response"],
    ]) {
        const page = browser();
        await page.respond(undefined, response);
        assert.equal(page.notice.hidden, true);
        assert.equal(page.warnings[0][1].failure, failure);
        assert.equal(page.warnings[0][1].status, status);
        page.jump(45000);
        assert.equal(page.notice.dataset.liveError, failure);
        page.fire("focus");
        await page.respond();
        assert.equal(page.notice.hidden, true);
    }
    const page = browser();
    const setAttribute = page.root.setAttribute;
    page.root.setAttribute = () => { throw new Error("DOM update unavailable"); };
    await page.respond(fixture(5000));
    assert.equal(page.warnings[0][1].failure, "render");
    page.jump(45000);
    assert.equal(page.notice.dataset.liveError, "render");
    page.root.setAttribute = setAttribute;
    page.fire("focus");
    await page.respond();
    assert.equal(page.notice.hidden, true);
});

test("failed syncs retain observations, expire schedules, and recover", async () => {
    const page = browser();
    page.popup.open = true;
    page.document.activeElement = page.button;
    await page.respond();
    page.jump(5000);
    page.requests.at(-1).reject(new Error("Offline"));
    await page.settle();
    assert.equal(page.notice.textContent, "Reconnecting");
    assert.equal(page.notice.hidden, true);
    page.jump(116000);
    assert.equal(page.schedule.textContent, "Schedule stale");
    assert.equal(page.notice.hidden, false);
    assert.equal(page.notice.dataset.liveError, "network");
    assert.equal(page.uptime.textContent, "Uptime unavailable");
    assert.equal(page.progress.hidden, false);
    assert.equal(page.progress.textContent, "Fetching pageviews (2,000/13,500)");
    assert.equal(page.statusLabel.textContent, "Updating records for October 20, 2026");
    assert.equal(page.button.textContent, "Details…");
    assert.equal(page.button.hidden, false);
    assert.equal(page.document.activeElement, page.button);
    assert.equal(page.popup.open, true);
    assert.equal(page.clock.textContent, "(2 min 11 sec…)");
    await page.respond();
    assert.equal(page.notice.hidden, true);
    assert.equal(page.schedule.hasAttribute("data-schedule-time"), true);
    assert.equal(page.progress.hidden, false);
    assert.equal(page.statusLabel.textContent, "Updating records for October 20, 2026");
    assert.equal(page.document.getElementById("status-button"), page.button);
    assert.equal(page.document.activeElement, page.button);
    assert.equal(page.popup.open, true);
    assert.equal(page.uptime.textContent, "3 min 41 sec");
});

test("malformed fragments do not replace received observations", async () => {
    const page = browser();
    await page.respond();
    page.jump(5000);
    const malformed = fixture(5000);
    malformed.root.setAttribute("data-server-time", "invalid");
    await page.respond(malformed);
    assert.equal(page.notice.textContent, "Reconnecting");
    assert.equal(page.notice.hidden, true);
    assert.equal(page.warnings[0][1].failure, "clock");
    assert.equal(page.root.dataset.serverTime, stamp(0));
    page.jump(1000);
    assert.equal(page.clock.textContent, "(16 sec…)");
});

test("logs synchronize only compact server state and uptime", async () => {
    const page = browser(true);
    const response = fixture();
    response.document.roots.push(fixture(0, false, true).root);
    await page.respond(response);
    assert.equal(page.notice.hidden, true);
    assert.equal(page.document.getElementById("tool-status"), null);
    page.jump(5000);
    assert.equal(page.requests.length, 2);
    assert.equal(page.uptime.textContent, "1 min 45 sec");
    await page.respond(fixture(5000)); // Missing the log page's expected fragment.
    assert.equal(page.notice.hidden, true);
    assert.equal(page.warnings[0][1].failure, "response");
    assert.equal(page.server.textContent, "Running");
    page.jump(10000);
    await page.respond();
    assert.equal(page.notice.hidden, true);
    page.jump(121000);
    assert.equal(page.server.textContent, "Server unavailable");
    assert.equal(page.uptime.textContent, "Uptime unavailable");
    assert.equal(page.document.getElementById("tool-status"), null);
});

test("Logs monitor refreshes resources and expires stale usage", async () => {
    const page = browser(true, true);
    await page.respond();
    page.jump(5000);
    const next = fixture(5000, false, true, true);
    next.memory.textContent = "110.0 MiB";
    await page.respond(next);
    assert.equal(page.memory.textContent, "110.0 MiB");
    assert.equal(page.document.getElementById("server-runtime"), page.runtime);
    page.jump(121000);
    assert.equal(page.memory.textContent, "Unknown");
    assert.equal(page.server.textContent, "Server unavailable");
});

test("About footer ticks, synchronizes, and expires without status panels", async () => {
    const page = browser(false, false, { footerOnly: true });
    for (const id of ["tool-status", "live-server-status", "server-runtime"]) {
        assert.equal(page.document.getElementById(id), null);
    }
    assert.equal(page.uptime.textContent, "1 min 40 sec");
    await page.respond();
    page.jump(1000);
    assert.equal(page.uptime.textContent, "1 min 41 sec");
    page.jump(4000);
    assert.equal(page.requests.length, 2);
    const next = fixture(5000, false, true, true);
    next.uptime.setAttribute("data-started-at", stamp(-200000));
    await page.respond(next);
    assert.equal(page.document.getElementById("live-uptime"), page.uptime);
    assert.equal(page.uptime.dataset.serverTime, stamp(5000));
    assert.equal(page.uptime.textContent, "3 min 25 sec");
    for (const id of ["tool-status", "live-server-status", "server-runtime"]) {
        assert.equal(page.document.getElementById(id), null);
    }
    page.jump(121000);
    assert.equal(page.uptime.textContent, "Uptime unavailable");
});

test("uptime clocks omit leading zero units across duration boundaries", async () => {
    for (const [seconds, expected] of [
        [0, "0 sec"], [59, "59 sec"], [60, "1 min 0 sec"],
        [3600, "1 hr 0 min 0 sec"], [86400, "1 d 0 hr 0 min 0 sec"],
    ]) {
        const page = browser(false, false, { footerOnly: true });
        const next = fixture(0, false, false, false, true);
        next.uptime.setAttribute("data-started-at", stamp(-seconds * 1000));
        await page.respond(next);
        assert.equal(page.uptime.textContent, expected);
    }
});

test("legacy uptime fragments retain their existing full-duration template", async () => {
    const page = browser(false, false, { footerOnly: true });
    const next = fixture(0, false, false, false, true);
    next.uptime.removeAttribute("data-uptime-compact-template");
    await page.respond(next);
    assert.equal(page.uptime.textContent, "0d 0h 1m 40s");
});

test("native WebSocket pushes preserve controls and use the page locale", async () => {
    const page = browser(false, false, { websocket: true, language: "ZH_HANT" });
    const socket = page.sockets[0];
    assert.equal(socket.url, "wss://aranami.toolforge.org/api/live/ws?lang=zh-hant");
    socket.open();
    page.popup.open = true;
    page.document.activeElement = page.button;
    await page.respond();
    page.jump(1000);
    page.push(fixture(1000, true));
    assert.equal(page.document.getElementById("status-button"), page.button);
    assert.equal(page.document.activeElement, page.button);
    assert.equal(page.popup.open, true);
    assert.equal(page.statusLabel.textContent, "No update");
    assert.equal(page.clock.textContent, "(14.30 sec)");
    assert.equal(page.clock.hasAttribute("data-clock"), false);
    assert.equal(page.notice.hidden, true);
    assert.equal(page.requests.length, 1);
    const plain = browser(false, false, { websocket: true, protocol: "http:" });
    assert.equal(plain.sockets[0].url, "ws://aranami.toolforge.org/api/live/ws?lang=en");
});

test("HTTP verifies healthy pushes every thirty seconds despite heartbeats", async () => {
    const page = browser(false, false, { websocket: true });
    page.sockets[0].open();
    page.push();
    await page.respond();
    page.jump(15000);
    page.frame({ type: "heartbeat" });
    assert.equal(page.requests.length, 1);
    page.jump(14999);
    page.frame({ type: "heartbeat" });
    assert.equal(page.requests.length, 1);
    page.jump(1);
    assert.equal(page.requests.length, 2);
    await page.respond();
    page.jump(15000);
    page.frame({ type: "heartbeat" });
    page.jump(15000);
    assert.equal(page.requests.length, 3);
    assert.equal(page.sockets.length, 1);
    assert.equal(page.notice.hidden, true);
});

test("a delayed HTTP snapshot cannot overwrite a more recent pushed result", async () => {
    const page = browser(false, false, { websocket: true });
    page.sockets[0].open();
    page.jump(1000);
    page.push(fixture(1000, true));
    await page.respond(fixture(0));
    assert.equal(page.statusLabel.textContent, "No update");
    assert.equal(page.clock.textContent, "(14.30 sec)");
    assert.equal(page.root.dataset.serverTime, stamp(1000));
    page.push(fixture(0)); // The same monotonic rule applies to pushed snapshots.
    assert.equal(page.statusLabel.textContent, "No update");
    assert.equal(page.warnings.length, 0);
});

test("socket retries never postpone independent fallback HTTP polling", async () => {
    const page = browser(false, false, { websocket: true });
    page.sockets[0].disconnect();
    await page.respond();
    assert.equal(page.notice.hidden, true);
    page.jump(1000);
    assert.equal(page.sockets.length, 2);
    page.sockets[1].disconnect();
    page.jump(2000);
    assert.equal(page.sockets.length, 3);
    page.sockets[2].disconnect();
    page.jump(2000);
    assert.equal(page.requests.length, 2);
    assert.equal(page.sockets.length, 3);
    await page.respond();
    assert.equal(page.notice.hidden, true);
    assert.equal(page.notice.hasAttribute("data-live-error"), false);
    assert.equal(page.warnings.length, 1);
    assert.equal(page.warnings[0][1].transport, "websocket");
});

test("socket reconnect backoff includes jitter and remains bounded", async () => {
    for (const [random, delay] of [[0, 800], [1, 1200]]) {
        const page = browser(false, false, { websocket: true, random });
        await page.respond();
        page.sockets[0].disconnect();
        page.jump(delay - 1);
        assert.equal(page.sockets.length, 1);
        page.jump(1);
        assert.equal(page.sockets.length, 2);
    }
    const page = browser(false, false, { websocket: true, random: 1 });
    for (const delay of [1200, 2400, 4800, 9600, 19200, 30000, 30000]) {
        page.sockets.at(-1).disconnect();
        page.jump(delay - 1);
        const count = page.sockets.length;
        page.jump(1);
        assert.equal(page.sockets.length, count + 1);
    }
});

test("intermittent healthy reconnects cannot postpone an existing HTTP deadline", async () => {
    const page = browser(false, false, { websocket: true });
    page.sockets[0].open();
    page.push();
    await page.respond();
    for (let round = 0; round < 3; round++) {
        page.jump(10000);
        page.sockets.at(-1).disconnect();
        page.jump(1000);
        page.sockets.at(-1).open();
        page.frame({ type: "heartbeat" });
        page.jump(3999);
        assert.equal(page.requests.length, round + 1);
        page.jump(1);
        assert.equal(page.requests.length, round + 2);
        await page.respond();
    }
});

test("the watchdog measures valid frames rather than socket activity", async () => {
    const page = browser(false, false, { websocket: true });
    const socket = page.sockets[0];
    socket.open();
    page.push();
    await page.respond();
    page.jump(30000);
    page.frame({ type: "heartbeat" });
    await page.respond();
    page.jump(30000);
    assert.equal(socket.closes, 0);
    await page.respond();
    page.jump(15000);
    assert.equal(socket.closes, 1);
    assert.equal(page.warnings.at(-1)[1].failure, "timeout");
    assert.equal(page.notice.hidden, true); // HTTP still verified the saved state.
    page.jump(1000);
    assert.equal(page.sockets.length, 2);
});

test("malformed pushed frames retain state and reconnect with diagnostics", async () => {
    for (const frame of ["not JSON", { type: "unknown" }, { type: "status", html: 123 }]) {
        const page = browser(false, false, { websocket: true });
        const socket = page.sockets[0];
        socket.open();
        await page.respond();
        page.frame(frame);
        assert.equal(socket.closes, 1);
        assert.equal(page.root.dataset.serverTime, stamp(0));
        assert.equal(page.notice.hidden, true);
        assert.equal(page.warnings[0][1].transport, "websocket");
        assert.equal(page.warnings[0][1].failure, typeof frame.html === "number" ? "response" : "protocol");
        page.jump(1000);
        assert.equal(page.sockets.length, 2);
        page.sockets[1].open();
        page.push();
        assert.equal(page.notice.hidden, true);
    }
    const page = browser(false, false, { websocket: true });
    const invalid = fixture();
    invalid.root.setAttribute("data-clock-messages", "{}");
    page.push(invalid);
    assert.equal(page.warnings[0][1].failure, "clock");
    assert.equal(page.root.dataset.clockMessages, JSON.stringify(messages));
});

test("healthy heartbeats do not conceal missing full status verification", async () => {
    const page = browser(false, false, { websocket: true });
    page.sockets[0].open();
    page.push();
    await page.respond();
    page.jump(30000);
    page.frame({ type: "heartbeat" });
    page.requests.at(-1).reject(new Error("HTTP unavailable"));
    await page.settle();
    assert.equal(page.notice.hidden, true);
    page.jump(15000);
    page.frame({ type: "heartbeat" });
    assert.equal(page.notice.hidden, false);
    assert.equal(page.notice.dataset.liveError, "network");
    page.push();
    assert.equal(page.notice.hidden, true);
    assert.equal(page.notice.hasAttribute("data-live-error"), false);
});

test("suspension closes sockets, aborts HTTP, and ignores late old events", async () => {
    const page = browser(false, false, { websocket: true });
    const old = page.sockets[0];
    old.open();
    const lateMessage = old.onmessage;
    const lateClose = old.onclose;
    page.document.hidden = true;
    page.fire("visibilitychange");
    await page.settle();
    assert.equal(old.closes, 1);
    assert.equal(page.requests[0].options.signal.aborted, true);
    page.jump(60000);
    assert.equal(page.sockets.length, 1);
    assert.equal(page.requests.length, 1);
    page.document.hidden = false;
    page.fire("visibilitychange");
    assert.equal(page.sockets.length, 2);
    assert.equal(page.requests.length, 2);
    lateMessage({ data: "not JSON" });
    lateClose();
    assert.equal(page.sockets[1].closes, 0);
    assert.equal(page.warnings.length, 0);
    await page.respond();
    page.sockets[1].open();
    page.push();
    page.fire("pagehide");
    assert.equal(page.sockets[1].closes, 1);
    page.jump(60000);
    assert.equal(page.sockets.length, 2);
    page.fire("pageshow");
    assert.equal(page.sockets.length, 3);
    assert.equal(page.requests.length, 3);
});

test("focus recovers overdue HTTP and socket deadlines before timers run", async () => {
    const page = browser(false, false, { websocket: true });
    const socket = page.sockets[0];
    socket.open();
    page.elapse(61000);
    page.fire("focus");
    assert.equal(socket.closes, 1);
    assert.equal(page.sockets.length, 2);
    assert.equal(page.requests[0].options.signal.aborted, true);
    assert.equal(page.requests.length, 1);
    await page.settle();
    page.jump(0);
    assert.equal(page.requests.length, 2);
    page.fire("online");
    assert.equal(page.sockets.length, 2);
    assert.equal(page.requests.length, 2);
    await page.respond();
    assert.equal(page.notice.hidden, true);
});


test("process restart accepts a reset sequence but rejects retired responses", async () => {
    const page = browser(false, false, { websocket: true });
    page.frame(page.envelope(fixture(1000), {sequence: 10}));
    page.frame(page.envelope(fixture(0, true), {instance: "c".repeat(32), sequence: 1}));
    assert.equal(page.statusLabel.textContent, "No update");
    page.frame(page.envelope(fixture(2000), {sequence: 99}));
    assert.equal(page.statusLabel.textContent, "No update");
    assert.equal(page.root.dataset.serverTime, stamp(0));
});

test("new protocol versions never replace the last valid view", async () => {
    const page = browser(false, false, { websocket: true });
    page.push(fixture(1000, true));
    page.frame(page.envelope(fixture(2000), {schema: 2}));
    assert.equal(page.statusLabel.textContent, "No update");
    assert.equal(page.warnings.at(-1)[1].failure, "protocol");
});

test("unavailable push preserves observations and requests HTTP recovery", async () => {
    const page = browser(false, false, { websocket: true });
    page.push(fixture(1000, true));
    await page.respond(fixture(1000, true));
    page.frame({type: "unavailable", error: "status_unavailable"});
    assert.equal(page.statusLabel.textContent, "No update");
    assert.equal(page.notice.hidden, false);
    page.jump(1000);
    assert.equal(page.requests.length, 2);
    await page.respond(fixture(2000, true));
    assert.equal(page.notice.hidden, true);
});

test("conditional HTTP verification keeps the accepted view", async () => {
    const page = browser();
    await page.respond(fixture(0, true), {headers: {get: key => key === "Content-Type" ? "application/json" : '"generation-1"'}});
    page.jump(5000);
    assert.equal(page.requests.at(-1).options.headers["If-None-Match"], '"generation-1"');
    await page.respond(undefined, {ok: false, status: 304});
    assert.equal(page.statusLabel.textContent, "No update");
    assert.equal(page.notice.hidden, true);
});

test("newer sequence accepts a backward clock adjustment", async () => {
    const page = browser(false, false, {websocket: true});
    page.frame(page.envelope(fixture(10000), {sequence: 3, revision: 5}));
    page.frame(page.envelope(fixture(1000, true), {sequence: 4, revision: 6}));
    assert.equal(page.statusLabel.textContent, "No update");
    assert.equal(page.root.dataset.serverTime, stamp(1000));
});

test("an unseen process cannot roll back the same saved store", async () => {
    const page = browser(false, false, {websocket: true});
    page.frame(page.envelope(fixture(1000, true), {revision: 8}));
    page.frame(page.envelope(fixture(2000), {instance: "c".repeat(32), revision: 7}));
    assert.equal(page.statusLabel.textContent, "No update");
    page.frame(page.envelope(fixture(3000), {instance: "d".repeat(32), store_id: "e".repeat(32), revision: 1}));
    assert.equal(page.root.dataset.serverTime, stamp(3000));
});

test("service heartbeats expire independently", async () => {
    const page = browser();
    const fresh = fixture(0);
    const first = new Element("SPAN", {id: "first-service", "data-observation-expires": stamp(1000), "data-stale-text": "Stale"}, "Current");
    const second = new Element("SPAN", {id: "second-service", "data-observation-expires": stamp(3000), "data-stale-text": "Stale"}, "Current");
    fresh.root.insertBefore(first, null);
    fresh.root.insertBefore(second, null);
    await page.respond(fresh);
    page.jump(2000);
    assert.equal(page.document.getElementById("first-service").textContent, "Stale");
    assert.equal(page.document.getElementById("second-service").textContent, "Current");
});

test("active execution duration expires with its owning service", async () => {
    const page = browser();
    const next = fixture();
    next.clock.setAttribute("data-clock-expires", stamp(1000));
    next.clock.setAttribute("data-clock-unknown", "—");
    await page.respond(next);
    page.jump(2000);
    assert.equal(page.clock.textContent, "—");
    assert.equal(page.uptime.textContent, "1 min 42 sec");
});


test("live refresh preserves heading-copy focus and pending clipboard state", async () => {
    const page = browser();
    const control = new Element('BUTTON', {id: 'heading-copy-server-status-heading', class: 'heading-copy', disabled: '', 'aria-label': 'Copy section'} , '#');
    const heading = new Element('H2', {id: 'server-status-heading', class: 'has-heading-link', 'aria-label': 'Server status'}, '', [control, new Text('Server status')]);
    page.root.insertBefore(heading, page.root.firstChild);
    page.document.activeElement = control;
    const next = fixture(5000);
    next.root.insertBefore(new Element('H2', {id: 'server-status-heading'}, 'Server status'), next.root.firstChild);
    await page.respond(next);
    assert.equal(page.document.getElementById('server-status-heading'), heading);
    assert.equal(page.document.getElementById('heading-copy-server-status-heading'), control);
    assert.equal(page.document.activeElement, control);
    assert.equal(control.hasAttribute('disabled'), true);
    assert.equal(heading.getAttribute('class'), 'has-heading-link');
    assert.equal(heading.getAttribute('aria-label'), 'Server status');
    assert.equal(heading.childNodes.filter(node => node.nodeName === 'BUTTON').length, 1);
});


test("Server status panels refresh resource values every second", async () => {
    const page = browser(true, true, {fastResources: true});
    await page.respond();
    page.jump(1000);
    assert.equal(page.requests.length, 2);
    const next = fixture(1000, false, true, true);
    next.memory.textContent = "125.0 MiB";
    await page.respond(next);
    assert.equal(page.memory.textContent, "125.0 MiB");
    page.jump(1000);
    assert.equal(page.requests.length, 3);
});


test("dataset update sections reconcile separately and elapsed clocks need no file requests", async () => {
    const page = browser(true, true, {datasetUpdates: true});
    await page.respond();
    assert.equal(page.datasetClock.textContent, "1:05.00");
    const requests = page.requests.length;
    page.jump(1250);
    assert.equal(page.datasetClock.textContent, "1:06.25");
    assert.equal(page.requests.length, requests);
    const next = fixture(1250, false, true, true, false, true);
    next.previousUpdates.textContent = "Completed new.parquet";
    next.currentUpdates.childNodes[1].childNodes[0].setAttribute("data-started-at", stamp(0));
    page.jump(3750);
    await page.respond(next);
    assert.equal(page.document.getElementById("server-runtime"), page.runtime);
    assert.equal(page.document.getElementById("dataset-updates-previous"), page.previousUpdates);
    assert.equal(page.previousUpdates.textContent, "Completed new.parquet");
    assert.equal(page.datasetClock.textContent, "0:01.25");
    assert.ok(page.requests.every(request => !String(request.url).startsWith("/data/")));
});

test("expired dataset evidence stops elapsed clocks and cannot retain a running heading", async () => {
    const page = browser(true, true, {datasetUpdates: true});
    await page.respond();
    page.jump(120000);
    assert.equal(page.datasetHeading.textContent, "Planned updates");
    assert.equal(page.datasetHeading.getAttribute("aria-label"), "Planned updates");
    assert.equal(page.datasetBody.textContent, "Progress unknown");
    assert.equal(page.document.getElementById("dataset-elapsed"), null);
});
