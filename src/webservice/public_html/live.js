/* Receive saved-status pushes and periodically verify them over HTTP. */
(() => {
    "use strict";

    // Native modal dialogs supply focus containment, Escape dismissal, and an
    // inert background. Delegation also covers buttons refreshed over HTTP.
    const dialogOpeners = new Map();
    document.addEventListener("click", (event) => {
        const button = event.target.closest?.("button[commandfor]");
        if (!button) return;
        const dialog = document.getElementById(button.getAttribute("commandfor"));
        if (dialog?.nodeName !== "DIALOG" || typeof dialog.showModal !== "function") return;
        const command = button.getAttribute("command");
        if (command === "show-modal") {
            event.preventDefault();
            if (!dialog.open) {
                dialogOpeners.set(dialog, button);
                dialog.showModal();
            }
        } else if (command === "close") {
            event.preventDefault();
            dialog.close();
        }
    });
    document.addEventListener("close", (event) => {
        const opener = dialogOpeners.get(event.target);
        dialogOpeners.delete(event.target);
        if (opener?.isConnected) opener.focus();
    }, true);
    document.addEventListener("keydown", (event) => {
        if (event.key !== "Tab") return;
        const dialog = event.target.closest?.("dialog[open]");
        if (!dialog) return;
        const controls = [...dialog.querySelectorAll(
            "a[href],button,input,select,textarea,[tabindex]",
        )].filter((node) => node.tabIndex >= 0 && !node.disabled && node.getClientRects().length);
        const first = controls[0];
        const last = controls.at(-1);
        if (!first) {
            event.preventDefault();
        } else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });

    const clockId = ["tool-status", "live-server-status", "live-uptime"]
        .find((id) => document.getElementById(id));
    if (!clockId || !document.getElementById("live-uptime")) return;
    const regionIds = ["tool-status", "live-uptime", "live-server-status", "server-runtime"]
        .filter((id) => document.getElementById(id));

    const interval = 5000;
    const verificationInterval = 30000;
    const requestTimeout = 10000;
    const socketTimeout = 45000;
    let timer;
    let pollAt;
    let clockTimer;
    let pending;
    let retry = interval;
    let active = true;
    let refreshAgain = false;
    const lastFailures = new Map();
    let statusFailure;
    let lastStatusAt = -Infinity;
    let socket;
    let socketTimer;
    let watchdogTimer;
    let lastMessageAt;
    let socketHealthy = false;
    let socketRetry = 1000;
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
            "time.days_ago", "time.days_ahead", "time.soon", "time.just_now",
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
        const root = document.getElementById(clockId);
        const expires = Date.parse(root.dataset.scheduleExpires);
        const stale = Number.isFinite(expires) && now > expires;
        if (stale) {
            for (const id of regionIds) {
                document.getElementById(id).querySelectorAll("[data-schedule-time]").forEach((node) => {
                    node.textContent = node.dataset.staleText || root.dataset.scheduleStale;
                    node.removeAttribute("data-schedule-time");
                });
            }
            root.querySelectorAll("[data-progress-active]").forEach((node) => {
                node.hidden = true;
            });
        }
        root.querySelectorAll("[data-progress-label]").forEach((node) => {
            const text = stale ? node.dataset.progressFallback : node.dataset.progressLabel;
            if (node.textContent !== text) node.textContent = text;
        });
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
                    const unit = minutes >= 1440 ? "days" : minutes >= 60 ? "hours" : "minutes";
                    const direction = difference > 0 ? "ahead" : "ago";
                    const message = minutes === 0
                        ? (difference > 0 ? "time.soon" : "time.just_now")
                        : `time.${unit}_${direction}`;
                    text = format(anchor.messages[message], {
                        count: unit === "days" ? Math.floor(minutes / 1440)
                            : unit === "hours" ? Math.floor(minutes / 60) : minutes,
                    });
                    if (node.hasAttribute("data-relative-inline")) text = text.slice(1, -1);
                } else {
                    const elapsed = (now - Date.parse(node.dataset.startedAt)) / 1000;
                    if (!Number.isFinite(elapsed)) continue;
                    const seconds = Math.max(0, elapsed);
                    const whole = Math.floor(seconds);
                    if (node.dataset.clock === "uptime") {
                        const values = {
                            days: Math.floor(whole / 86400),
                            hours: Math.floor(whole / 3600) % 24,
                            minutes: Math.floor(whole / 60) % 60,
                            seconds: whole % 60,
                        };
                        const unit = whole >= 86400 ? "days" : whole >= 3600 ? "hours"
                            : whole >= 60 ? "minutes" : "seconds";
                        const duration = anchor.messages[`duration.${unit}`];
                        const compact = node.dataset.uptimeCompactTemplate && typeof duration === "string";
                        text = stale ? node.dataset.uptimeUnknown : format(
                            compact ? node.dataset.uptimeCompactTemplate : node.dataset.uptimeTemplate, {
                                ...values,
                                ...(compact ? { duration: format(duration, values) } : {}),
                            },
                        );
                    } else {
                        const minutes = Math.floor(whole / 60);
                        const duration = format(
                            anchor.messages[minutes ? "duration.minutes" : "duration.seconds"],
                            {
                                minutes,
                                seconds: minutes ? String(whole % 60).padStart(2, "0") : whole,
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
        updateNotice();
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
            // The server describes closed dialogs; preserve the user's modal
            // state while refreshing the observations inside them.
            if (current.nodeName === "DIALOG" && attribute.name === "open") continue;
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

    function notice(connected, failure) {
        for (const id of ["live-notice", "live-server-notice"]) {
            const label = document.getElementById(id);
            if (!label) continue;
            label.hidden = connected;
            if (connected) label.removeAttribute("data-live-error");
            else label.setAttribute("data-live-error", failure);
        }
    }

    function updateNotice() {
        // Heartbeats keep the socket alive; only a complete applied snapshot
        // establishes fresh status, through either transport.
        const fresh = performance.now() - lastStatusAt < socketTimeout;
        notice(fresh, statusFailure || "stale");
    }

    function reportFailure(transport, failure, status) {
        if (!active || document.hidden) return;
        statusFailure = failure;
        updateNotice();
        const detail = { transport, failure, ...(status ? { status } : {}) };
        const signature = JSON.stringify(detail);
        if (lastFailures.get(transport) !== signature) {
            console.warn("Aranami live refresh failed:", detail);
            lastFailures.set(transport, signature);
        }
    }

    class StatusError extends Error {
        constructor(failure) {
            super("Invalid live status");
            this.failure = failure;
        }
    }

    // Both transports validate the complete snapshot before changing the page.
    // A delayed HTTP response must not overwrite a more recent pushed status.
    function applySnapshot(html) {
        let fragments;
        try {
            if (typeof html !== "string" || html.length > 8 * 1024 * 1024) {
                throw new Error("Invalid status response");
            }
            const next = new DOMParser().parseFromString(html, "text/html");
            fragments = regionIds.map((id) => next.getElementById(id));
            if (fragments.some((fragment) => !fragment)) throw new Error("Incomplete status");
        } catch {
            throw new StatusError("response");
        }
        let nextAnchor;
        try {
            nextAnchor = readClock(fragments[regionIds.indexOf(clockId)]);
        } catch {
            throw new StatusError("clock");
        }
        if (anchor && nextAnchor.serverTime < anchor.serverTime) return false;
        try {
            fragments.forEach((fragment, index) => {
                reconcile(document.getElementById(regionIds[index]), fragment);
            });
            anchor = nextAnchor;
            updateClocks();
        } catch {
            throw new StatusError("render");
        }
        lastStatusAt = performance.now();
        statusFailure = null;
        notice(true);
        return true;
    }

    function scheduleRefresh(delay, soonerOnly = false) {
        if (!active || document.hidden || pending) return;
        const at = performance.now() + delay;
        if (soonerOnly && timer && pollAt <= at) return;
        clearTimeout(timer);
        pollAt = at;
        timer = setTimeout(refresh, delay);
    }

    async function refresh() {
        clearTimeout(timer);
        timer = null;
        if (!active || document.hidden || pending) return;
        const controller = new AbortController();
        const request = { controller, startedAt: performance.now(), expired: false };
        pending = request;
        let failure = "network";
        let status;
        const timeout = setTimeout(() => {
            request.expired = true;
            controller.abort();
        }, requestTimeout);
        try {
            const response = await fetch("/api/live", {
                cache: "no-store",
                credentials: "same-origin",
                headers: { "Accept-Language": document.documentElement.lang },
                signal: controller.signal,
            });
            if (!response.ok) {
                failure = "http";
                status = response.status;
                throw new Error("Status unavailable");
            }
            failure = "response";
            if (!response.headers.get("Content-Type")?.startsWith("text/html")) {
                throw new Error("Status unavailable");
            }
            const html = await response.text();
            if (!active || document.hidden || controller.signal.aborted) return;
            applySnapshot(html);
            retry = interval;
            lastFailures.delete("http");
        } catch (error) {
            if (!controller.signal.aborted || request.expired) {
                if (request.expired) failure = "timeout";
                else if (error instanceof StatusError) failure = error.failure;
                reportFailure("http", failure, status);
                retry = Math.min(retry * 2, 30000);
            }
        } finally {
            clearTimeout(timeout);
            pending = null;
            if (active && !document.hidden) {
                scheduleRefresh(refreshAgain ? 0 : socketHealthy ? verificationInterval : retry);
            }
            refreshAgain = false;
        }
    }

    function closeSocket() {
        clearTimeout(watchdogTimer);
        const previous = socket;
        socket = null;
        socketHealthy = false;
        if (previous) {
            previous.onopen = previous.onmessage = previous.onerror = previous.onclose = null;
            previous.close();
        }
    }

    function reconnectSocket() {
        clearTimeout(socketTimer);
        if (!active || document.hidden || typeof WebSocket !== "function") return;
        const delay = Math.round(Math.min(30000, socketRetry * (0.8 + Math.random() * 0.4)));
        socketRetry = Math.min(socketRetry * 2, 30000);
        socketTimer = setTimeout(connectSocket, delay);
    }

    function loseSocket(current, failure) {
        if (current !== socket) return;
        closeSocket();
        reportFailure("websocket", failure);
        scheduleRefresh(retry, true);
        reconnectSocket();
    }

    function watchSocket(current) {
        clearTimeout(watchdogTimer);
        watchdogTimer = setTimeout(() => loseSocket(current, "timeout"), socketTimeout);
    }

    function connectSocket() {
        clearTimeout(socketTimer);
        if (!active || document.hidden || socket || typeof WebSocket !== "function") return;
        const lang = document.documentElement.lang.toLowerCase().replaceAll("_", "-");
        const locale = ["en", "zh", "zh-hans", "zh-hant"].includes(lang) ? lang : "en";
        const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
        let current;
        try {
            current = new WebSocket(`${scheme}//${window.location.host}/api/live/ws?lang=${locale}`);
        } catch {
            reportFailure("websocket", "connect");
            reconnectSocket();
            return;
        }
        socket = current;
        lastMessageAt = performance.now();
        watchSocket(current);
        current.onmessage = (event) => {
            if (socket !== current || !active || document.hidden) return;
            try {
                if (typeof event.data !== "string" || event.data.length > 8 * 1024 * 1024) {
                    throw new Error("Invalid status frame");
                }
                const frame = JSON.parse(event.data);
                if (!frame || typeof frame !== "object") throw new Error("Invalid status frame");
                if (frame.type === "status") applySnapshot(frame.html);
                else if (frame.type !== "heartbeat") throw new Error("Unknown status frame");
                lastMessageAt = performance.now();
                watchSocket(current);
                if (!socketHealthy) {
                    socketHealthy = true;
                    scheduleRefresh(verificationInterval, true);
                }
                socketRetry = 1000;
                lastFailures.delete("websocket");
            } catch (error) {
                loseSocket(current, error instanceof StatusError ? error.failure : "protocol");
            }
        };
        current.onerror = () => loseSocket(current, "network");
        current.onclose = () => loseSocket(current, "closed");
    }

    function suspend() {
        clearTimeout(timer);
        timer = null;
        clearTimeout(clockTimer);
        pending?.controller.abort();
        clearTimeout(socketTimer);
        closeSocket();
    }

    function resume() {
        if (!active || document.hidden) return;
        tick();
        // Focus/network events can arrive before timers delayed by browser sleep.
        // Retire an overdue request before waiting for its timeout callback.
        if (pending && performance.now() - pending.startedAt >= requestTimeout) {
            pending.expired = true;
            pending.controller.abort();
        }
        if (pending?.controller.signal.aborted) refreshAgain = true;
        else refresh();
        if (socket && performance.now() - lastMessageAt >= socketTimeout) {
            loseSocket(socket, "timeout");
        } else if (socket && socket.readyState > WebSocket.OPEN) {
            loseSocket(socket, "closed");
        }
        connectSocket();
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
    window.addEventListener("focus", resume);
    try {
        anchor = readClock(document.getElementById(clockId));
        lastStatusAt = performance.now();
    } catch {
        // The complete server-rendered page still works without local clocks.
    }
    notice(true);
    tick();
    refresh();
    connectSocket();
})();
