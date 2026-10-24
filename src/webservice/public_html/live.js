/* Refresh saved status without disturbing native popovers, focus, or scroll. */
(() => {
    "use strict";

    const regionIds = ["tool-status", "live-uptime"];
    if (!regionIds.every((id) => document.getElementById(id))) return;

    const interval = 1000;
    let timer;
    let pending;
    let retry = interval;
    let active = true;

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
        if (!label) return;
        label.hidden = false;
        const text = connected ? label.dataset.live : label.dataset.reconnecting;
        if (label.textContent !== text) label.textContent = text;
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
            if (!active || document.hidden || controller.signal.aborted) return;
            // The connection notice is client state, not a saved observation.
            const label = next.getElementById("live-notice");
            if (label) label.hidden = false;
            fragments.forEach((fragment, index) => {
                reconcile(document.getElementById(regionIds[index]), fragment);
            });
            retry = interval;
            notice(true);
        } catch {
            if (active && !document.hidden) notice(false);
            retry = Math.min(retry * 2, 30000);
        } finally {
            clearTimeout(timeout);
            pending = null;
            if (active && !document.hidden) timer = setTimeout(refresh, retry);
        }
    }

    function suspend() {
        clearTimeout(timer);
        pending?.abort();
    }

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) suspend();
        else refresh();
    });
    window.addEventListener("pagehide", () => {
        active = false;
        suspend();
    });
    window.addEventListener("pageshow", () => {
        active = true;
        refresh();
    });
    window.addEventListener("online", refresh);
    notice(true);
    refresh();
})();
