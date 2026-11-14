(() => {
    "use strict";
    const dialogOpeners = /* @__PURE__ */ new Map();
    document.addEventListener("click", (event) => {
        const control = event.target.closest?.(
            'a[aria-haspopup="dialog"],button[commandfor]',
        );
        if (!control) return;
        if (control.hasAttribute("data-dataset-link")) return;
        const link = control.nodeName === "A";
        if (
            link &&
            (event.ctrlKey ||
                event.metaKey ||
                event.shiftKey ||
                event.altKey ||
                event.button > 0)
        )
            return;
        const dialog = document.getElementById(
            control.getAttribute(link ? "aria-controls" : "commandfor"),
        );
        if (
            dialog?.nodeName !== "DIALOG" ||
            typeof dialog.showModal !== "function"
        )
            return;
        const command = link ? "show-modal" : control.getAttribute("command");
        if (command === "show-modal") {
            event.preventDefault();
            if (!dialog.open) {
                dialogOpeners.set(dialog, control);
                dialog.showModal();
            }
        } else if (command === "close") {
            event.preventDefault();
            dialog.close();
        }
    });
    document.addEventListener(
        "close",
        (event) => {
            const opener = dialogOpeners.get(event.target);
            dialogOpeners.delete(event.target);
            if (opener?.isConnected) opener.focus();
        },
        true,
    );
    document.addEventListener("keydown", (event) => {
        if (event.key !== "Tab") return;
        const dialog = event.target.closest?.("dialog[open]");
        if (!dialog) return;
        const controls = [
            ...dialog.querySelectorAll(
                "a[href],button,input,select,textarea,[tabindex]",
            ),
        ].filter(
            (node) =>
                node.tabIndex >= 0 &&
                !node.disabled &&
                node.getClientRects().length,
        );
        const first = controls[0];
        const last = controls.at(-1);
        if (!first) {
            event.preventDefault();
        } else if (
            event.shiftKey &&
            (document.activeElement === first ||
                document.activeElement === dialog)
        ) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });
    function openFragmentDialog() {
        const fragment = window.location.hash?.slice(1);
        if (!fragment) return;
        const dialog = document.getElementById(fragment);
        if (dialog?.nodeName === "DIALOG" && !dialog.open) dialog.showModal();
    }
    window.addEventListener("hashchange", openFragmentDialog);
    openFragmentDialog();
    const clockId = ["tool-status", "live-server-status", "live-uptime"].find(
        (id) => document.getElementById(id),
    );
    if (!clockId || !document.getElementById("live-uptime")) return;
    const regionIds = [
        "tool-status",
        "live-uptime",
        "live-server-status",
        "server-runtime",
        "dataset-updates-previous",
        "dataset-updates-current",
    ].filter((id) => document.getElementById(id));
    const interval = document.getElementById("live-server-status")?.dataset.refreshInterval === "1000" ? 1000 : 5000;
    const verificationInterval = 3e4;
    const requestTimeout = 1e4;
    const socketTimeout = 45e3;
    let timer = null;
    let pollAt = Infinity;
    let clockTimer;
    let pending = null;
    let retry = interval;
    let active = true;
    let refreshAgain = false;
    const lastFailures = /* @__PURE__ */ new Map();
    let statusFailure = null;
    let lastStatusAt = -Infinity;
    let socket = null;
    let socketTimer;
    let watchdogTimer;
    let lastMessageAt = -Infinity;
    let socketHealthy = false;
    let socketRetry = 1e3;
    let anchor;
    let snapshotInstance = null;
    let snapshotSequence = -1;
    let snapshotStore = null;
    let snapshotRevision = null;
    let httpEtag = null;
    const retiredInstances = /* @__PURE__ */ new Set();
    function readClock(root) {
        const serverTime = Date.parse(root.dataset.serverTime);
        const messages = JSON.parse(root.dataset.clockMessages);
        if (
            !Number.isFinite(serverTime) ||
            !messages ||
            typeof messages !== "object"
        ) {
            throw new Error("Invalid clock");
        }
        for (const key2 of [
            "duration.seconds",
            "duration.minutes",
            "time.minutes_ago",
            "time.hours_ago",
            "time.weeks_ago",
            "time.months_ago",
            "time.years_ago",
            "time.minutes_ahead",
            "time.hours_ahead",
            "time.weeks_ahead",
            "time.months_ahead",
            "time.years_ahead",
            "time.days_ago",
            "time.days_ahead",
            "time.soon",
            "time.just_now",
        ]) {
            if (typeof messages[key2] !== "string")
                throw new Error("Invalid clock labels");
        }
        return { serverTime, messages, receivedAt: performance.now() };
    }
    const format = (template, values) =>
        template.replace(/\{([a-z]+)\}/g, (match, key2) =>
            String(values[key2] ?? match),
        );
    function relativeUnit(minutes) {
        for (const [unit, divisor] of [
            ["years", 525960],
            ["months", 43830],
            ["weeks", 10080],
            ["days", 1440],
            ["hours", 60],
        ]) {
            if (minutes >= divisor)
                return { unit, count: Math.floor(minutes / divisor) };
        }
        return { unit: "minutes", count: minutes };
    }
    function updateClocks() {
        if (!anchor) return;
        const now = anchor.serverTime + performance.now() - anchor.receivedAt;
        const root = document.getElementById(clockId);
        const expires = Date.parse(root.dataset.scheduleExpires);
        const stale = Number.isFinite(expires) && now > expires;
        if (stale) {
            for (const id of regionIds) {
                document
                    .getElementById(id)
                    .querySelectorAll("[data-schedule-time]")
                    .forEach((node) => {
                        node.textContent =
                            node.dataset.staleText ||
                            root.dataset.scheduleStale;
                        node.removeAttribute("data-schedule-time");
                    });
            }
        }
        for (const id of regionIds) {
            const region = document.getElementById(id);
            for (const observation of region.querySelectorAll(
                "[data-observation-expires]",
            )) {
                const expires2 = Date.parse(
                    observation.dataset.observationExpires,
                );
                if (Number.isFinite(expires2) && now >= expires2) {
                    observation.textContent =
                        observation.dataset.staleText || "";
                    if (observation.nodeName === "H2")
                        observation.setAttribute("aria-label", observation.textContent);
                    observation.removeAttribute("data-observation-expires");
                }
            }
            const nodes = [...region.querySelectorAll("[data-clock]")];
            if (region.dataset.clock) nodes.push(region);
            for (const node of nodes) {
                let text;
                const expires2 = Date.parse(node.dataset.clockExpires);
                if (Number.isFinite(expires2) && now >= expires2) {
                    text = node.dataset.clockUnknown || "\u2014";
                } else if (node.dataset.clock === "relative") {
                    const difference =
                        Date.parse(node.dataset.observedAt) - now;
                    if (!Number.isFinite(difference)) continue;
                    const minutes = Math.floor(Math.abs(difference) / 6e4);
                    const { unit, count } = relativeUnit(minutes);
                    const direction = difference > 0 ? "ahead" : "ago";
                    const message =
                        minutes === 0
                            ? difference > 0
                                ? "time.soon"
                                : "time.just_now"
                            : `time.${unit}_${direction}`;
                    const label =
                        count === 1 && anchor.messages[`${message}_one`]
                            ? `${message}_one`
                            : message;
                    text = format(anchor.messages[label], { count });
                    if (node.hasAttribute("data-relative-inline"))
                        text = text.slice(1, -1);
                } else {
                    const elapsed =
                        (now - Date.parse(node.dataset.startedAt)) / 1e3;
                    if (!Number.isFinite(elapsed)) continue;
                    const seconds = Math.max(0, elapsed);
                    const whole = Math.floor(seconds);
                    if (node.dataset.clock === "dataset-elapsed") {
                        const centiseconds = Math.floor(seconds * 100);
                        text = `${Math.floor(centiseconds / 6000)}:${String(Math.floor(centiseconds / 100) % 60).padStart(2, "0")}.${String(centiseconds % 100).padStart(2, "0")}`;
                    } else if (node.dataset.clock === "uptime") {
                        const values = {
                            days: Math.floor(whole / 86400),
                            hours: Math.floor(whole / 3600) % 24,
                            minutes: Math.floor(whole / 60) % 60,
                            seconds: whole % 60,
                        };
                        const unit =
                            whole >= 86400
                                ? "days"
                                : whole >= 3600
                                  ? "hours"
                                  : whole >= 60
                                    ? "minutes"
                                    : "seconds";
                        const duration = anchor.messages[`duration.${unit}`];
                        const compact =
                            node.dataset.uptimeCompactTemplate &&
                            typeof duration === "string";
                        text = stale
                            ? node.dataset.uptimeUnknown
                            : format(
                                  compact
                                      ? node.dataset.uptimeCompactTemplate
                                      : node.dataset.uptimeTemplate,
                                  {
                                      ...values,
                                      ...(compact
                                          ? {
                                                duration: format(
                                                    duration,
                                                    values,
                                                ),
                                            }
                                          : {}),
                                  },
                              );
                    } else {
                        const unit =
                            whole >= 86400
                                ? "days"
                                : whole >= 3600
                                  ? "hours"
                                  : whole >= 60
                                    ? "minutes"
                                    : "seconds";
                        const duration = `${format(
                            anchor.messages[`duration.${unit}`],
                            {
                                days: Math.floor(whole / 86400),
                                hours: Math.floor(whole / 3600) % 24,
                                minutes: Math.floor(whole / 60) % 60,
                                seconds: whole % 60,
                            },
                        )}\u2026`;
                        text = format(node.dataset.durationTemplate, {
                            duration,
                        });
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
        const datasetClocks = [...(document.getElementById("dataset-updates-current")?.querySelectorAll("[data-clock]") || [])].some(node => node.dataset.clock === "dataset-elapsed");
        clockTimer = setTimeout(tick, datasetClocks ? 100 : 1000);
    }
    const key = (node) =>
        node.nodeType === Node.ELEMENT_NODE
            ? node.id || node.getAttribute("data-key")
            : null;
    const compatible = (left, right) =>
        left &&
        left.nodeType === right.nodeType &&
        left.nodeName === right.nodeName &&
        key(left) === key(right);
    function reconcile(current, next) {
        if (current.nodeType === Node.TEXT_NODE) {
            const currentText = current;
            const nextText = next;
            if (currentText.data !== nextText.data)
                currentText.data = nextText.data;
            return;
        }
        const currentElement = current;
        const nextElement = next;
        if (
            current.nodeName === "H2" &&
            currentElement.id === nextElement.id
        ) {
            const control = [...current.childNodes].find(
                (child) =>
                    child.nodeType === Node.ELEMENT_NODE &&
                    child.getAttribute("class") === "heading-copy",
            );
            if (control) {
                const label =
                    nextElement.textContent?.trim() || nextElement.id;
                nextElement.setAttribute("aria-label", label);
                nextElement.setAttribute(
                    "class",
                    [nextElement.getAttribute("class"), "has-heading-link"]
                        .filter(Boolean)
                        .join(" "),
                );
                next.insertBefore(control.cloneNode(true), next.firstChild);
            }
        }
        for (const attribute of [...currentElement.attributes]) {
            if (current.nodeName === "DIALOG" && attribute.name === "open")
                continue;
            if (!nextElement.hasAttribute(attribute.name)) {
                currentElement.removeAttribute(attribute.name);
            }
        }
        for (const attribute of nextElement.attributes) {
            if (
                currentElement.getAttribute(attribute.name) !== attribute.value
            ) {
                currentElement.setAttribute(attribute.name, attribute.value);
            }
        }
        let cursor = current.firstChild;
        for (const child of next.childNodes) {
            let match = compatible(cursor, child) ? cursor : null;
            if (!match && key(child)) {
                match =
                    [...current.childNodes].find((node) =>
                        compatible(node, child),
                    ) || null;
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
            else {
                label.setAttribute("data-live-error", failure);
                label.textContent =
                    failure === "stale"
                        ? label.dataset.staleText || label.textContent
                        : label.dataset.disconnectedText || label.textContent;
            }
        }
    }
    function updateNotice() {
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
        failure;
        /**
         * Attach a classified snapshot failure to a transport error.
         *
         * @param failure - Validation or rendering stage that failed.
         */
        constructor(failure) {
            super("Invalid live status");
            this.failure = failure;
        }
    }
    function applySnapshot(html) {
        let fragments;
        try {
            if (typeof html !== "string" || html.length > 8 * 1024 * 1024) {
                throw new Error("Invalid status response");
            }
            const next = new DOMParser().parseFromString(html, "text/html");
            const parsed = regionIds.map((id) => next.getElementById(id));
            if (parsed.some((fragment) => !fragment))
                throw new Error("Incomplete status");
            fragments = parsed;
        } catch {
            throw new StatusError("response");
        }
        let nextAnchor;
        try {
            nextAnchor = readClock(fragments[regionIds.indexOf(clockId)]);
        } catch {
            throw new StatusError("clock");
        }
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
    function applyFrame(value) {
        if (!value || typeof value !== "object")
            throw new StatusError("protocol");
        const frame = value;
        if (
            frame.schema !== 1 ||
            frame.type !== "status" ||
            typeof frame.instance !== "string" ||
            !/^[0-9a-f]{32}$/.test(frame.instance) ||
            !Number.isSafeInteger(frame.sequence) ||
            frame.sequence < 1 ||
            !(
                frame.store_id === null ||
                (typeof frame.store_id === "string" &&
                    /^[0-9a-f]{32}$/.test(frame.store_id))
            ) ||
            !(
                frame.revision === null ||
                (Number.isSafeInteger(frame.revision) && frame.revision >= 0)
            ) ||
            typeof frame.observed_at !== "string" ||
            !Number.isFinite(Date.parse(frame.observed_at))
        ) {
            throw new StatusError("protocol");
        }
        if (
            retiredInstances.has(frame.instance) ||
            (snapshotInstance === frame.instance &&
                frame.sequence < snapshotSequence)
        )
            return false;
        if (
            frame.store_id !== null &&
            frame.store_id === snapshotStore &&
            snapshotRevision !== null &&
            frame.revision !== null &&
            frame.revision < snapshotRevision
        )
            return false;
        const applied = applySnapshot(frame.html);
        if (applied) {
            if (snapshotInstance && snapshotInstance !== frame.instance) {
                retiredInstances.add(snapshotInstance);
                if (retiredInstances.size > 16)
                    retiredInstances.delete(
                        retiredInstances.values().next().value,
                    );
            }
            snapshotInstance = frame.instance;
            snapshotSequence = frame.sequence;
            snapshotStore = frame.store_id;
            snapshotRevision = frame.revision;
        }
        return applied;
    }
    function scheduleRefresh(delay, soonerOnly = false) {
        if (!active || document.hidden || pending) return;
        const at = performance.now() + delay;
        if (soonerOnly && timer && pollAt <= at) return;
        clearTimeout(timer ?? void 0);
        pollAt = at;
        timer = setTimeout(refresh, delay);
    }
    async function refresh() {
        clearTimeout(timer ?? void 0);
        timer = null;
        if (!active || document.hidden || pending) return;
        const controller = new AbortController();
        const request = {
            controller,
            startedAt: performance.now(),
            expired: false,
        };
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
                headers: {
                    "Accept-Language": document.documentElement.lang,
                    ...(httpEtag ? { "If-None-Match": httpEtag } : {}),
                },
                signal: controller.signal,
            });
            if (response.status === 304) {
                lastStatusAt = performance.now();
                statusFailure = null;
                notice(true);
                retry = interval;
                return;
            }
            if (!response.ok) {
                failure = "http";
                status = response.status;
                throw new Error("Status unavailable");
            }
            failure = "response";
            if (
                !response.headers
                    .get("Content-Type")
                    ?.startsWith("application/json")
            ) {
                throw new Error("Status unavailable");
            }
            const frame = await response.json();
            if (!active || document.hidden || controller.signal.aborted)
                return;
            if (applyFrame(frame)) httpEtag = response.headers.get("ETag");
            retry = interval;
            lastFailures.delete("http");
        } catch (error) {
            if (!controller.signal.aborted || request.expired) {
                if (request.expired) failure = "timeout";
                else if (error instanceof StatusError) failure = error.failure;
                reportFailure("http", failure, status);
                retry = Math.min(retry * 2, 3e4);
            }
        } finally {
            clearTimeout(timeout);
            pending = null;
            if (active && !document.hidden) {
                scheduleRefresh(
                    refreshAgain
                        ? 0
                        : socketHealthy
                          ? verificationInterval
                          : retry,
                );
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
            previous.onopen =
                previous.onmessage =
                previous.onerror =
                previous.onclose =
                    null;
            previous.close();
        }
    }
    function reconnectSocket() {
        clearTimeout(socketTimer);
        if (!active || document.hidden || typeof WebSocket !== "function")
            return;
        const delay = Math.round(
            Math.min(3e4, socketRetry * (0.8 + Math.random() * 0.4)),
        );
        socketRetry = Math.min(socketRetry * 2, 3e4);
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
        watchdogTimer = setTimeout(
            () => loseSocket(current, "timeout"),
            socketTimeout,
        );
    }
    function connectSocket() {
        clearTimeout(socketTimer);
        if (
            !active ||
            document.hidden ||
            socket ||
            typeof WebSocket !== "function"
        )
            return;
        const lang = document.documentElement.lang
            .toLowerCase()
            .replaceAll("_", "-");
        const locale = [
            "en",
            "zh",
            "zh-hans",
            "zh-hant",
            "zh-hant-tw",
            "zh-hant-hk",
        ].includes(lang)
            ? lang
            : "en";
        const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
        let current;
        try {
            current = new WebSocket(
                `${scheme}//${window.location.host}/api/live/ws?lang=${locale}`,
            );
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
                if (
                    typeof event.data !== "string" ||
                    event.data.length > 8 * 1024 * 1024
                ) {
                    throw new Error("Invalid status frame");
                }
                const frame = JSON.parse(event.data);
                if (
                    !frame ||
                    typeof frame !== "object" ||
                    frame.schema !== 1 ||
                    typeof frame.instance !== "string" ||
                    !/^[0-9a-f]{32}$/.test(frame.instance) ||
                    !Number.isSafeInteger(frame.sequence)
                )
                    throw new Error("Invalid status frame");
                if (frame.type === "status") applyFrame(frame);
                else if (frame.type === "unavailable") {
                    reportFailure("websocket", "response");
                    notice(false, "response");
                    scheduleRefresh(1e3, true);
                } else if (frame.type !== "heartbeat")
                    throw new Error("Unknown status frame");
                lastMessageAt = performance.now();
                watchSocket(current);
                if (!socketHealthy) {
                    socketHealthy = true;
                    scheduleRefresh(verificationInterval, true);
                }
                socketRetry = 1e3;
                lastFailures.delete("websocket");
            } catch (error) {
                loseSocket(
                    current,
                    error instanceof StatusError ? error.failure : "protocol",
                );
            }
        };
        current.onerror = () => loseSocket(current, "network");
        current.onclose = () => loseSocket(current, "closed");
    }
    function suspend() {
        clearTimeout(timer ?? void 0);
        timer = null;
        clearTimeout(clockTimer);
        pending?.controller.abort();
        clearTimeout(socketTimer);
        closeSocket();
    }
    function resume() {
        if (!active || document.hidden) return;
        tick();
        if (
            pending &&
            performance.now() - pending.startedAt >= requestTimeout
        ) {
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
    } catch {}
    notice(true);
    tick();
    refresh();
    connectSocket();
})();
