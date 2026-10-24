/* Advance local clocks and periodically synchronize saved status. */
(() => {
    "use strict";

    const regionIds = ["tool-status", "live-uptime"];
    if (!regionIds.every((id) => document.getElementById(id))) return;

    const interval = 15000;
    let timer;
    let clockTimer;
    let pending;
    let retry = interval;
    let active = true;
    let refreshAgain = false;
    let anchor;

    function readClock(root) {
        const serverTime = Date.parse(root.dataset.serverTime);
        const messages = JSON.parse(root.dataset.clockMessages);
        if (!Number.isFinite(serverTime) || !messages || typeof messages !== "object") {
            throw new Error("Invalid clock");
        }
        for (const key of [
            "duration.seconds", "duration.minutes", "time.minutes_ago",
            "time.hours_ago", "time.minutes_ahead", "time.hours_ahead",
            "time.soon", "time.just_now",
        ]) {
            if (typeof messages[key] !== "string") throw new Error("Invalid clock labels");
        }
        return { serverTime, messages, receivedAt: performance.now() };
    }

    const format = (template, values) => template.replace(
        /\{([a-z]+)\}/g, (match, key) => values[key] ?? match,
    );

    function updateClocks() {
        if (!anchor) return;
        // A delayed browser callback advances by actual elapsed time. Changing
        // the computer's wall clock cannot speed up or reverse these timers.
        const now = anchor.serverTime + performance.now() - anchor.receivedAt;
        const root = document.getElementById("tool-status");
        const expires = Date.parse(root.dataset.scheduleExpires);
        const stale = Number.isFinite(expires) && now > expires;
        if (stale) {
            root.querySelectorAll("[data-schedule-time]").forEach((node) => {
                node.textContent = root.dataset.scheduleStale;
                node.removeAttribute("data-schedule-time");
            });
        }
        for (const id of regionIds) {
            const region = document.getElementById(id);
            const nodes = [...region.querySelectorAll("[data-clock]")];
            if (region.dataset.clock) nodes.push(region);
            for (const node of nodes) {
                let text;
                if (node.dataset.clock === "relative") {
                    const difference = Date.parse(node.dataset.observedAt) - now;
                    if (!Number.isFinite(difference)) continue;
                    const minutes = Math.floor(Math.abs(difference) / 60000);
                    const unit = minutes >= 60 ? "hours" : "minutes";
                    const direction = difference > 0 ? "ahead" : "ago";
                    const message = minutes === 0
                        ? (difference > 0 ? "time.soon" : "time.just_now")
                        : `time.${unit}_${direction}`;
                    text = format(anchor.messages[message], {
                        count: minutes >= 60 ? Math.floor(minutes / 60) : minutes,
                    });
                } else {
                    const elapsed = (now - Date.parse(node.dataset.startedAt)) / 1000;
                    if (!Number.isFinite(elapsed)) continue;
                    const seconds = Math.max(0, elapsed);
                    const whole = Math.floor(seconds);
                    if (node.dataset.clock === "uptime") {
                        text = stale ? node.dataset.uptimeUnknown : format(
                            node.dataset.uptimeTemplate, {
                                days: Math.floor(whole / 86400),
                                hours: Math.floor(whole / 3600) % 24,
                                minutes: Math.floor(whole / 60) % 60,
                                seconds: whole % 60,
                            },
                        );
                    } else {
                        const duration = format(
                            anchor.messages[whole >= 60 ? "duration.minutes" : "duration.seconds"],
                            {
                                minutes: Math.floor(whole / 60),
                                seconds: whole >= 60 ? whole % 60
                                    : (seconds < 1 ? seconds.toFixed(2) : whole),
                            },
                        ) + "…";
                        text = format(node.dataset.durationTemplate, { duration });
                    }
                }
                if (node.textContent !== text) node.textContent = text;
            }
        }
    }

    function tick() {
        clearTimeout(clockTimer);
        if (!active || document.hidden) return;
        updateClocks();
        clockTimer = setTimeout(tick, 1000);
    }

    const key = (node) => node.nodeType === Node.ELEMENT_NODE
        ? node.id || node.getAttribute("data-key")
        : null;
    const compatible = (left, right) => left
        && left.nodeType === right.nodeType
        && left.nodeName === right.nodeName
        && key(left) === key(right);

    // Keep existing elements in place so browser-managed popup/focus state
    // survives each response. Destination rows retain their page identities.
    function reconcile(current, next) {
        if (current.nodeType === Node.TEXT_NODE) {
            if (current.data !== next.data) current.data = next.data;
            return;
        }
        for (const attribute of [...current.attributes]) {
            if (!next.hasAttribute(attribute.name)) {
                current.removeAttribute(attribute.name);
            }
        }
        for (const attribute of next.attributes) {
            if (current.getAttribute(attribute.name) !== attribute.value) {
                current.setAttribute(attribute.name, attribute.value);
            }
        }
        let cursor = current.firstChild;
        for (const child of next.childNodes) {
            let match = compatible(cursor, child) ? cursor : null;
            if (!match && key(child)) {
                match = [...current.childNodes].find((node) => compatible(node, child));
            }
            if (!match) {
                match = child.cloneNode(true);
                current.insertBefore(match, cursor);
            } else {
                if (match !== cursor) current.insertBefore(match, cursor);
                reconcile(match, child);
            }
            cursor = match.nextSibling;
        }
        while (cursor) {
            const removed = cursor;
            cursor = cursor.nextSibling;
            removed.remove();
        }
    }

    function notice(connected) {
        const label = document.getElementById("live-notice");
        if (label) label.hidden = connected;
    }

    async function refresh() {
        clearTimeout(timer);
        if (!active || document.hidden || pending) return;
        const controller = new AbortController();
        pending = controller;
        const timeout = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch("/api/live", {
                cache: "no-store",
                credentials: "same-origin",
                headers: { "Accept-Language": document.documentElement.lang },
                signal: controller.signal,
            });
            if (!response.ok || !response.headers.get("Content-Type")?.startsWith("text/html")) {
                throw new Error("Status unavailable");
            }
            const next = new DOMParser().parseFromString(await response.text(), "text/html");
            const fragments = regionIds.map((id) => next.getElementById(id));
            if (fragments.some((fragment) => !fragment)) throw new Error("Incomplete status");
            const nextAnchor = readClock(fragments[0]);
            if (!active || document.hidden || controller.signal.aborted) return;
            fragments.forEach((fragment, index) => {
                reconcile(document.getElementById(regionIds[index]), fragment);
            });
            anchor = nextAnchor;
            updateClocks();
            retry = interval;
            notice(true);
        } catch {
            if (active && !document.hidden) notice(false);
            retry = Math.min(retry * 2, 30000);
        } finally {
            clearTimeout(timeout);
            pending = null;
            if (active && !document.hidden) {
                timer = setTimeout(refresh, refreshAgain ? 0 : retry);
            }
            refreshAgain = false;
        }
    }

    function suspend() {
        clearTimeout(timer);
        clearTimeout(clockTimer);
        pending?.abort();
    }

    function resume() {
        if (!active || document.hidden) return;
        tick();
        if (pending?.signal.aborted) refreshAgain = true;
        else refresh();
    }

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) suspend();
        else resume();
    });
    window.addEventListener("pagehide", () => {
        active = false;
        suspend();
    });
    window.addEventListener("pageshow", () => {
        active = true;
        resume();
    });
    window.addEventListener("online", resume);
    try {
        anchor = readClock(document.getElementById("tool-status"));
    } catch {
        // The complete server-rendered page still works without local clocks.
    }
    notice(true);
    tick();
    refresh();
})();
